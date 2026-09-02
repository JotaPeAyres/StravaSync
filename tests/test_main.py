"""Testes do ponto de entrada (Fase 6): isolamento de falhas por corredor.

O `SyncService` real fala com o Strava; aqui ele é substituído por um duplo
que só registra chamadas e dispara o que mandarmos — o que se testa é a
*política* do `main.py` (quem pula, quem conta como falha, quem para tudo),
não a sincronização em si (isso é `test_sync_service.py`).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src import main as modulo_main
from src.models.corredor import Corredor
from src.repositories.corredor_state_repository import CorredorStateRepository
from src.services.database_service import DatabaseService
from src.utils.config import Config, ConfigError
from src.utils.errors import AuthorizationError, QuotaExhaustedError, RevokedTokenError


def _corredor(identificador: str) -> Corredor:
    return Corredor(
        id=identificador,
        nome=identificador,
        refresh_token="token",
        start_date=date(2026, 1, 1),
        cutover_date=date(2026, 1, 1),
        excel_path=Path(f"{identificador}.xlsx"),
    )


def _config(database_path: Path, *ids: str) -> Config:
    return Config(
        strava_client_id="12345",
        strava_client_secret="segredo",
        template_path=Path("template.xlsx"),
        database_path=database_path,
        corredores=tuple(_corredor(i) for i in ids),
    )


class _SyncServiceFalso:
    """Substitui o `SyncService` real: registra quem visitou, dispara o combinado."""

    def __init__(self) -> None:
        self.visitados: list[str] = []
        self._erros: dict[str, Exception] = {}

    def falhar_com(self, corredor_id: str, erro: Exception) -> None:
        self._erros[corredor_id] = erro

    def sincronizar(self, corredor: Corredor) -> None:
        self.visitados.append(corredor.id)
        if corredor.id in self._erros:
            raise self._erros[corredor.id]


@pytest.fixture
def sem_logging(monkeypatch):
    """O main não deve reconfigurar o logging global durante os testes."""
    monkeypatch.setattr(modulo_main, "setup_logging", lambda **_kwargs: None)


def _usar(monkeypatch, config: Config, fake: _SyncServiceFalso) -> None:
    monkeypatch.setattr(modulo_main, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(modulo_main, "_montar_sync_service", lambda *_a, **_k: fake)


def test_sai_com_codigo_1_quando_a_config_falha(monkeypatch, capsys):
    def falhar(*_args, **_kwargs):
        raise ConfigError("STRAVA_CLIENT_ID não definida")

    monkeypatch.setattr(modulo_main, "load_config", falhar)

    assert modulo_main.main() == 1
    assert "STRAVA_CLIENT_ID" in capsys.readouterr().err


def test_config_error_nao_escapa_do_main(monkeypatch):
    """O usuário recebe mensagem tratada, não um traceback."""

    def falhar(*_args, **_kwargs):
        raise ConfigError("qualquer coisa")

    monkeypatch.setattr(modulo_main, "load_config", falhar)

    try:
        modulo_main.main()
    except ConfigError:  # pragma: no cover
        pytest.fail("ConfigError vazou do main()")


def test_percorre_todos_os_corredores(monkeypatch, sem_logging, tmp_path):
    config = _config(tmp_path / "estado.db", "p001", "p002", "p003")
    fake = _SyncServiceFalso()
    _usar(monkeypatch, config, fake)

    assert modulo_main.main() == 0
    assert fake.visitados == ["p001", "p002", "p003"]


def test_falha_de_um_corredor_nao_interrompe_os_demais(monkeypatch, sem_logging, tmp_path, caplog):
    """Com dezenas de participantes, um bug num corredor não pode parar a coleta."""
    config = _config(tmp_path / "estado.db", "p001", "p002", "p003")
    fake = _SyncServiceFalso()
    fake.falhar_com("p002", RuntimeError("bug inesperado"))
    _usar(monkeypatch, config, fake)

    assert modulo_main.main() == 1
    assert fake.visitados == ["p001", "p002", "p003"]
    assert "p002" in caplog.text


def test_authorization_error_conta_como_falha_e_continua(monkeypatch, sem_logging, tmp_path):
    config = _config(tmp_path / "estado.db", "p001", "p002")
    fake = _SyncServiceFalso()
    fake.falhar_com("p001", AuthorizationError("code expirado"))
    _usar(monkeypatch, config, fake)

    assert modulo_main.main() == 1
    assert fake.visitados == ["p001", "p002"]


def test_revoked_token_conta_como_falha_e_continua(monkeypatch, sem_logging, tmp_path):
    config = _config(tmp_path / "estado.db", "p001", "p002")
    fake = _SyncServiceFalso()
    fake.falhar_com("p001", RevokedTokenError("revogado"))
    _usar(monkeypatch, config, fake)

    assert modulo_main.main() == 1
    assert fake.visitados == ["p001", "p002"]


def test_quota_esgotada_para_a_execucao_sem_contar_falha(monkeypatch, sem_logging, tmp_path):
    """Não é falha de corredor — os que faltam ficam pendentes, não falharam."""
    config = _config(tmp_path / "estado.db", "p001", "p002", "p003")
    fake = _SyncServiceFalso()
    fake.falhar_com("p002", QuotaExhaustedError("cota esgotada"))
    _usar(monkeypatch, config, fake)

    assert modulo_main.main() == 0
    assert fake.visitados == ["p001", "p002"]  # p003 nunca chega a ser tentado


def test_quota_esgotada_loga_os_pendentes(monkeypatch, sem_logging, tmp_path, caplog):
    config = _config(tmp_path / "estado.db", "p001", "p002", "p003")
    fake = _SyncServiceFalso()
    fake.falhar_com("p002", QuotaExhaustedError("cota esgotada"))
    _usar(monkeypatch, config, fake)

    modulo_main.main()

    assert "p002" in caplog.text
    assert "p003" in caplog.text


def test_corredor_marcado_para_reinscricao_e_pulado_sem_gastar_requisicao(
    monkeypatch, sem_logging, tmp_path
):
    caminho = tmp_path / "estado.db"
    banco = DatabaseService(caminho)
    banco.init_schema()
    CorredorStateRepository(banco.connect()).marcar_reinscricao("p001", "token revogado")
    banco.close()

    config = _config(caminho, "p001", "p002")
    fake = _SyncServiceFalso()
    _usar(monkeypatch, config, fake)

    assert modulo_main.main() == 0
    assert fake.visitados == ["p002"]  # p001 nunca chega a ser chamado


def test_corredor_pulado_nao_conta_como_falha_desta_execucao(monkeypatch, sem_logging, tmp_path):
    caminho = tmp_path / "estado.db"
    banco = DatabaseService(caminho)
    banco.init_schema()
    CorredorStateRepository(banco.connect()).marcar_reinscricao("p001", "token revogado")
    banco.close()

    config = _config(caminho, "p001")
    fake = _SyncServiceFalso()
    _usar(monkeypatch, config, fake)

    assert modulo_main.main() == 0
    assert fake.visitados == []
