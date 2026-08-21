"""Testes da CLI de inscrição (Fase 3).

Aqui o "usuário" é o operador da pesquisa. O que se verifica é o que ele vê na
tela e o código de saída — inclusive que nenhum token cru apareça impresso.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from src import inscricao as modulo
from src.api.rate_limiter import RateLimiter
from src.api.strava_client import StravaClient
from src.models.corredor import Corredor
from src.utils.config import Config

URL_DE_RETORNO = (
    "http://localhost/exchange_token?state=p001&code=abc123&scope=read,activity:read_all"
)


class _Servidor:
    def __init__(self, *respostas: httpx.Response) -> None:
        self.respostas = list(respostas)
        self.requisicoes: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requisicoes.append(request)
        if not self.respostas:
            raise AssertionError(f"requisição inesperada: {request.url}")
        return self.respostas.pop(0)


def _token_json(athlete_id: int = 777) -> dict:
    return {
        "access_token": "access-secretissimo",
        "refresh_token": "refresh-secretissimo",
        "expires_at": int((datetime.now(UTC) + timedelta(hours=6)).timestamp()),
        "athlete": {"id": athlete_id},
    }


def _erro_strava(recurso: str) -> httpx.Response:
    return httpx.Response(400, json={"errors": [{"resource": recurso, "code": "invalid"}]})


def _corredor(identificador: str) -> Corredor:
    return Corredor(
        id=identificador,
        nome="Ana" if identificador == "p001" else identificador,
        refresh_token="token-do-toml",
        start_date=date(2026, 1, 1),
        cutover_date=date(2026, 1, 1),
        excel_path=Path(f"{identificador}.xlsx"),
    )


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """Prepara a CLI com config falsa, banco em tmp_path e logging neutralizado."""
    config = Config(
        strava_client_id="12345",
        strava_client_secret="segredo",
        template_path=Path("template.xlsx"),
        database_path=tmp_path / "estado.db",
        corredores=(_corredor("p001"), _corredor("p002")),
        strava_pausa_entre_chamadas_s=0,
    )
    monkeypatch.setattr(modulo, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(modulo, "setup_logging", lambda **_kwargs: None)
    return config


def _com_servidor(monkeypatch, servidor: _Servidor) -> None:
    """Faz a CLI usar um cliente com transporte simulado, sem tocar a rede."""
    def fabricar(config):
        return StravaClient(
            config.strava_client_id,
            config.strava_client_secret,
            limiter=RateLimiter(pausa_s=0, dormir=lambda _: None),
            http=httpx.Client(transport=httpx.MockTransport(servidor)),
        )

    monkeypatch.setattr(modulo, "_cliente", fabricar)


# ------------------------------------------------------------------ argumentos


def test_sem_subcomando_e_erro_de_uso(cli):
    with pytest.raises(SystemExit) as saida:
        modulo.main([])

    assert saida.value.code == 2


# ------------------------------------------------------------------------ link


def test_link_traz_o_state_do_corredor(cli, capsys):
    assert modulo.main(["link", "p001"]) == 0

    saida = capsys.readouterr().out
    assert "state=p001" in saida
    assert "scope=activity%3Aread_all" in saida
    assert "client_id=12345" in saida


def test_link_de_corredor_desconhecido_falha_com_dica(cli, capsys):
    assert modulo.main(["link", "p999"]) == 1

    erro = capsys.readouterr().err
    assert "p999" in erro
    assert "p001, p002" in erro


# ---------------------------------------------------------------------- trocar


def test_trocar_grava_o_estado(cli, monkeypatch, capsys):
    _com_servidor(monkeypatch, _Servidor(httpx.Response(200, json=_token_json())))

    assert modulo.main(["trocar", URL_DE_RETORNO]) == 0

    saida = capsys.readouterr().out
    assert "p001" in saida
    assert "777" in saida
    assert "read,activity:read_all" in saida
    # E o estado ficou gravado: o comando `estado` já enxerga.
    assert modulo.main(["estado", "p001"]) == 0


def test_trocar_nao_imprime_o_token(cli, monkeypatch, capsys):
    _com_servidor(monkeypatch, _Servidor(httpx.Response(200, json=_token_json())))

    modulo.main(["trocar", URL_DE_RETORNO])

    capturado = capsys.readouterr()
    assert "refresh-secretissimo" not in capturado.out
    assert "access-secretissimo" not in capturado.out


def test_code_expirado_manda_pedir_link_novo(cli, monkeypatch, capsys):
    """É o erro mais comum da inscrição: o `code` viaja por mensagem."""
    _com_servidor(monkeypatch, _Servidor(_erro_strava("AuthorizationCode")))

    assert modulo.main(["trocar", URL_DE_RETORNO]) == 1
    assert "link novo" in capsys.readouterr().err


def test_code_cru_e_recusado_por_escopo_nao_verificado(cli, monkeypatch, capsys):
    """Sem a URL não há como saber se o participante liberou as atividades."""
    _com_servidor(monkeypatch, _Servidor())

    assert modulo.main(["trocar", "abc123", "--corredor", "p001"]) == 1
    assert "escopo" in capsys.readouterr().err


def test_code_cru_pode_ser_forcado(cli, monkeypatch, capsys):
    _com_servidor(monkeypatch, _Servidor(httpx.Response(200, json=_token_json())))

    codigo = modulo.main(
        ["trocar", "abc123", "--corredor", "p001", "--sem-verificar-escopo"]
    )

    assert codigo == 0
    assert "NÃO VERIFICADO" in capsys.readouterr().out


def test_code_cru_sem_corredor_nao_adivinha(cli, monkeypatch, capsys):
    _com_servidor(monkeypatch, _Servidor())

    assert modulo.main(["trocar", "abc123"]) == 1
    assert "--corredor" in capsys.readouterr().err


def test_state_divergente_do_corredor_informado_e_recusado(cli, monkeypatch, capsys):
    """Gravar o token de um participante no cadastro de outro não tem volta."""
    _com_servidor(monkeypatch, _Servidor())

    assert modulo.main(["trocar", URL_DE_RETORNO, "--corredor", "p002"]) == 1

    erro = capsys.readouterr().err
    assert "p001" in erro
    assert "p002" in erro


def test_participante_que_recusou_a_autorizacao(cli, monkeypatch, capsys):
    _com_servidor(monkeypatch, _Servidor())

    codigo = modulo.main(
        ["trocar", "http://localhost/exchange_token?state=p001&error=access_denied"]
    )

    assert codigo == 1
    assert "recusou" in capsys.readouterr().err


# ---------------------------------------------------------------------- estado


def test_estado_lista_quem_falta_inscrever(cli, capsys):
    """Sai com 1 para poder virar checagem automática antes de uma coleta."""
    assert modulo.main(["estado"]) == 1

    saida = capsys.readouterr().out
    assert "p001" in saida
    assert "p002" in saida
    assert "não inscrito" in saida


def test_estado_mascara_o_token(cli, monkeypatch, capsys):
    _com_servidor(monkeypatch, _Servidor(httpx.Response(200, json=_token_json())))
    modulo.main(["trocar", URL_DE_RETORNO])
    capsys.readouterr()

    modulo.main(["estado", "p001"])

    saida = capsys.readouterr().out
    assert "refresh-secretissimo" not in saida
    assert "****" in saida


def test_estado_mostra_pendencia_de_reinscricao(cli, monkeypatch, capsys):
    _com_servidor(monkeypatch, _Servidor(httpx.Response(200, json=_token_json())))
    modulo.main(["trocar", URL_DE_RETORNO])

    from src.repositories.corredor_state_repository import CorredorStateRepository
    from src.services.database_service import DatabaseService

    banco = DatabaseService(cli.database_path)
    try:
        CorredorStateRepository(banco.connect()).marcar_reinscricao("p001", "token revogado")
    finally:
        banco.close()
    capsys.readouterr()

    assert modulo.main(["estado", "p001"]) == 1

    saida = capsys.readouterr().out
    assert "PRECISA REINSCREVER" in saida
    assert "token revogado" in saida
