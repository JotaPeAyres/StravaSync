"""Teste E2E (Fase 8): pilha 100% real para múltiplos corredores na mesma execução.

`test_main.py` testa a *política* de `main.sincronizar_todos` (isolamento de
falha, ordenação, alerta) com o `SyncService` substituído por um dublê.
`test_sync_service.py` testa a *mecânica* real — `StravaClient`/`RateLimiter`
com `httpx.MockTransport`, `ExcelService` com o template real, SQLite real —
mas só para um corredor por teste. Este arquivo junta as duas coisas: uma
única instância de `RateLimiter`/`StravaClient`/`SyncService`, compartilhada
entre 2-3 corredores no mesmo processo, chamada por `main.sincronizar_todos`
(o seam público real, sem dublê nenhum). É a única prova empírica de que:

- o `RateLimiter`/`StravaClient` únicos são de fato compartilhados entre
  corredores (não um por corredor, o que derrotaria o controle de vazão);
- uma `QuotaExhaustedError` real, vinda do `StravaClient` real no meio do
  lote, interrompe a execução e deixa os corredores seguintes intocados;
- uma falha real de um corredor (planilha aberta) não contamina os demais.
"""
from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import openpyxl
import pytest

import src.services.excel_service as modulo_excel_service
from src import main as modulo_main
from src.api.rate_limiter import RateLimiter
from src.api.strava_client import StravaClient
from src.models.corredor import Corredor
from src.models.strava_token import StravaToken
from src.repositories.activity_repository import ActivityRepository
from src.repositories.corredor_state_repository import CorredorStateRepository
from src.repositories.escrita_excel_repository import EscritaExcelRepository
from src.services.activity_service import ActivityService
from src.services.adocao_service import AdocaoService
from src.services.database_service import DatabaseService
from src.services.excel_service import ABA_DAILY_DATA, ExcelService
from src.services.sync_service import SyncService
from src.utils.config import Config

TEMPLATE = Path(__file__).resolve().parents[1] / "Cópia de Planilha_carga_corrida.xlsx"
INICIO = date(2026, 1, 1)
URL_API = "https://api.exemplo/v3"
URL_OAUTH = "https://oauth.exemplo/token"

pytestmark = pytest.mark.skipif(
    not TEMPLATE.is_file(), reason="template real não encontrado no repositório"
)


class _ServidorMultiplex:
    """Roteia por corredor via `Authorization: Bearer access-<id>`.

    Um único `StravaClient` (e um único `RateLimiter`) atende todos os
    corredores da execução, como em produção — este servidor precisa então
    saber para qual corredor cada requisição pertence.
    """

    def __init__(self, respostas: dict[str, list[httpx.Response]]) -> None:
        self._respostas = {corredor_id: list(fila) for corredor_id, fila in respostas.items()}
        self.requisicoes: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requisicoes.append(request)
        corredor_id = _corredor_do_token(request)
        fila = self._respostas.get(corredor_id)
        if not fila:
            raise AssertionError(
                f"requisição inesperada para {corredor_id!r}: {request.method} {request.url}"
            )
        return fila.pop(0)


def _corredor_do_token(request: httpx.Request) -> str:
    cabecalho = request.headers.get("authorization", "")
    return cabecalho.removeprefix("Bearer ").removeprefix("access-")


def _resposta(status: int = 200, *, json=None, uso: str = "1,1") -> httpx.Response:
    cabecalhos = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": uso}
    return httpx.Response(status, json=json if json is not None else [], headers=cabecalhos)


def _corrida(identificador: int, dia: str, *, km: float = 10.0, minutos: float = 50.0) -> dict:
    return {
        "id": identificador,
        "name": f"Corrida {identificador}",
        "type": "Run",
        "start_date": f"{dia}T09:00:00Z",
        "start_date_local": f"{dia}T09:00:00Z",
        "distance": km * 1000,
        "moving_time": int(minutos * 60),
        "elapsed_time": int(minutos * 60) + 30,
    }


@pytest.fixture
def banco(tmp_path):
    servico = DatabaseService(tmp_path / "estado.db")
    servico.init_schema()
    try:
        yield servico
    finally:
        servico.close()


def _planilha(tmp_path: Path, corredor_id: str) -> Path:
    destino = tmp_path / f"{corredor_id}.xlsx"
    shutil.copy(TEMPLATE, destino)
    return destino


def _corredor(tmp_path: Path, corredor_id: str, *, cutover_date: date = INICIO) -> Corredor:
    return Corredor(
        id=corredor_id,
        nome=corredor_id,
        refresh_token=f"token-toml-{corredor_id}",
        start_date=INICIO,
        cutover_date=cutover_date,
        excel_path=_planilha(tmp_path, corredor_id),
    )


def _config(database_path: Path, *corredores: Corredor) -> Config:
    return Config(
        strava_client_id="12345",
        strava_client_secret="segredo",
        template_path=TEMPLATE,
        database_path=database_path,
        corredores=tuple(corredores),
    )


def _com_token_valido(banco, corredor_id: str) -> None:
    """Semeia um access token já válido — o teste não passa por `/oauth/token`."""
    CorredorStateRepository(banco.connect()).salvar_token(
        corredor_id,
        StravaToken(
            access_token=f"access-{corredor_id}",
            refresh_token=f"refresh-{corredor_id}",
            expira_em=datetime.now(UTC) + timedelta(hours=6),
            athlete_id=abs(hash(corredor_id)) % 100_000,
        ),
    )


def _servico_compartilhado(
    banco, servidor: _ServidorMultiplex, *, hoje: date, limiter: RateLimiter | None = None
) -> SyncService:
    """Monta o `SyncService` como `main._montar_sync_service` faz — uma única
    instância de `RateLimiter`/`StravaClient`, para ser reaproveitada por
    todos os corredores da chamada de `sincronizar_todos`."""
    conn = banco.connect()
    activity_repo = ActivityRepository(conn)
    excel_service = ExcelService()
    client = StravaClient(
        "12345",
        "segredo",
        limiter=limiter or RateLimiter(pausa_s=0, dormir=lambda _: None),
        http=httpx.Client(transport=httpx.MockTransport(servidor)),
        url_api=URL_API,
        url_oauth=URL_OAUTH,
    )
    return SyncService(
        client=client,
        activity_service=ActivityService(),
        activity_repository=activity_repo,
        corredor_state_repository=CorredorStateRepository(conn),
        excel_service=excel_service,
        escrita_excel_repository=EscritaExcelRepository(conn),
        adocao_service=AdocaoService(excel_service, activity_repo),
        hoje=lambda: hoje,
    )


def _daily_data(planilha: Path):
    return openpyxl.load_workbook(planilha)[ABA_DAILY_DATA]


# --------------------------------------------------------------- caminho feliz


def test_fluxo_feliz_com_tres_corredores_pilha_real(banco, tmp_path):
    corredores = [_corredor(tmp_path, cid) for cid in ("p001", "p002", "p003")]
    for corredor in corredores:
        _com_token_valido(banco, corredor.id)

    servidor = _ServidorMultiplex(
        {
            "p001": [_resposta(json=[_corrida(1, "2026-01-01", km=10.0)])],
            "p002": [_resposta(json=[_corrida(2, "2026-01-01", km=5.0)])],
            "p003": [_resposta(json=[])],
        }
    )
    sync_service = _servico_compartilhado(banco, servidor, hoje=INICIO)
    corredor_state = CorredorStateRepository(banco.connect())
    config = _config(banco.database_path, *corredores)

    falhas = modulo_main.sincronizar_todos(config, sync_service, corredor_state)

    assert falhas == 0

    repo = ActivityRepository(banco.connect())
    # Cada corredor só vê a própria corrida — nada vaza entre eles.
    assert [a.id for a in repo.por_dia("p001", INICIO)] == [1]
    assert [a.id for a in repo.por_dia("p002", INICIO)] == [2]
    assert repo.por_dia("p003", INICIO) == ()

    assert _daily_data(corredores[0].excel_path)["C2"].value == 10.0
    assert _daily_data(corredores[1].excel_path)["C2"].value == 5.0
    assert _daily_data(corredores[2].excel_path)["C2"].value == 0.0

    for corredor in corredores:
        assert corredor_state.buscar(corredor.id).ultima_sincronizacao is not None


# --------------------------------------------------------- cota compartilhada


def test_cota_esgotada_no_meio_do_lote_e_compartilhada_entre_corredores(banco, tmp_path):
    """Prova que o `RateLimiter` é um só: a cota que p001 gasta é a mesma que
    falta para p002 — com um limiter por corredor, isso não aconteceria."""
    corredores = [_corredor(tmp_path, cid) for cid in ("p001", "p002", "p003")]
    for corredor in corredores:
        _com_token_valido(banco, corredor.id)

    limiter = RateLimiter(pausa_s=0, dormir=lambda _: None)
    limiter.registrar_resposta({"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "1,999"})

    servidor = _ServidorMultiplex(
        {
            "p001": [_resposta(json=[_corrida(1, "2026-01-01", km=10.0)], uso="2,1000")],
            # p002 e p003 não devem gerar nenhuma requisição.
        }
    )
    sync_service = _servico_compartilhado(banco, servidor, hoje=INICIO, limiter=limiter)
    corredor_state = CorredorStateRepository(banco.connect())
    config = _config(banco.database_path, *corredores)

    falhas = modulo_main.sincronizar_todos(config, sync_service, corredor_state)

    assert falhas == 0  # cota esgotada não é falha de ninguém
    chamados = {_corredor_do_token(r) for r in servidor.requisicoes}
    assert chamados == {"p001"}  # p002/p003 nunca chegaram a ser tentados

    assert corredor_state.buscar("p001").ultima_sincronizacao is not None
    estado_p002 = corredor_state.buscar("p002")
    assert estado_p002 is None or estado_p002.falhas_consecutivas == 0


def test_segunda_execucao_retoma_os_pendentes_da_cota(banco, tmp_path):
    """Regressão do comportamento "self-healing" (Fase 6/7) com pilha real:
    uma nova execução, com a cota renovada, sincroniza quem ficou pendente."""
    corredores = [_corredor(tmp_path, cid) for cid in ("p001", "p002", "p003")]
    for corredor in corredores:
        _com_token_valido(banco, corredor.id)
    config = _config(banco.database_path, *corredores)
    corredor_state = CorredorStateRepository(banco.connect())

    limiter1 = RateLimiter(pausa_s=0, dormir=lambda _: None)
    limiter1.registrar_resposta({"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "1,999"})
    servidor1 = _ServidorMultiplex(
        {"p001": [_resposta(json=[_corrida(1, "2026-01-01", km=10.0)], uso="2,1000")]}
    )
    sync1 = _servico_compartilhado(banco, servidor1, hoje=INICIO, limiter=limiter1)
    modulo_main.sincronizar_todos(config, sync1, corredor_state)

    # Segunda execução: processo novo, RateLimiter zerado (nada persiste entre
    # execuções, de propósito), cota disponível de novo.
    servidor2 = _ServidorMultiplex(
        {
            "p002": [_resposta(json=[_corrida(2, "2026-01-01", km=5.0)])],
            "p003": [_resposta(json=[_corrida(3, "2026-01-01", km=3.0)])],
            "p001": [_resposta(json=[])],  # já sincronizado; fica por último
        }
    )
    sync2 = _servico_compartilhado(banco, servidor2, hoje=INICIO)
    falhas = modulo_main.sincronizar_todos(config, sync2, corredor_state)

    assert falhas == 0
    ordem = [_corredor_do_token(r) for r in servidor2.requisicoes]
    assert ordem == ["p002", "p003", "p001"]  # nunca sincronizados vêm primeiro
    assert _daily_data(corredores[1].excel_path)["C2"].value == 5.0
    assert _daily_data(corredores[2].excel_path)["C2"].value == 3.0


# --------------------------------------------------------------- isolamento


def test_falha_isolada_de_planilha_de_um_corredor_nao_contamina_os_demais(
    banco, tmp_path, monkeypatch
):
    corredores = [_corredor(tmp_path, cid) for cid in ("p001", "p002", "p003")]
    for corredor in corredores:
        _com_token_valido(banco, corredor.id)

    caminho_com_falha = corredores[1].excel_path
    original_replace = modulo_excel_service.os.replace

    def falhar_seletivamente(origem, destino, *args, **kwargs):
        if Path(destino) == caminho_com_falha:
            raise PermissionError("arquivo em uso")
        return original_replace(origem, destino, *args, **kwargs)

    monkeypatch.setattr(modulo_excel_service.os, "replace", falhar_seletivamente)

    servidor = _ServidorMultiplex(
        {
            "p001": [_resposta(json=[_corrida(1, "2026-01-01", km=10.0)])],
            "p002": [_resposta(json=[_corrida(2, "2026-01-01", km=7.0)])],
            "p003": [_resposta(json=[_corrida(3, "2026-01-01", km=3.0)])],
        }
    )
    sync_service = _servico_compartilhado(banco, servidor, hoje=INICIO)
    corredor_state = CorredorStateRepository(banco.connect())
    config = _config(banco.database_path, *corredores)

    falhas = modulo_main.sincronizar_todos(config, sync_service, corredor_state)

    assert falhas == 1
    assert _daily_data(corredores[0].excel_path)["C2"].value == 10.0
    assert _daily_data(corredores[2].excel_path)["C2"].value == 3.0

    # p002: a atividade chegou a ser gravada no SQLite (isso acontece antes da
    # tentativa de escrita no Excel), mas a planilha original não foi tocada.
    assert [a.id for a in ActivityRepository(banco.connect()).por_dia("p002", INICIO)] == [2]
    assert _daily_data(corredores[1].excel_path)["C2"].value is None
    assert corredor_state.buscar("p002").falhas_consecutivas == 1
    assert corredor_state.buscar("p002").ultima_sincronizacao is None

    # Nenhum arquivo temporário sobrou da tentativa que falhou.
    assert list(tmp_path.glob(".*.tmp.xlsx")) == []
