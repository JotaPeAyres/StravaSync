"""Testes do ponto de entrada (Fase 2): config, logging e isolamento de falhas."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src import main as modulo_main
from src.models.corredor import Corredor
from src.utils.config import Config, ConfigError


def _corredor(identificador: str) -> Corredor:
    return Corredor(
        id=identificador,
        nome=identificador,
        refresh_token="token",
        start_date=date(2026, 1, 1),
        cutover_date=date(2026, 1, 1),
        excel_path=Path(f"{identificador}.xlsx"),
    )


def _config(*ids: str) -> Config:
    return Config(
        strava_client_id="12345",
        strava_client_secret="segredo",
        template_path=Path("template.xlsx"),
        database_path=Path("banco.db"),
        corredores=tuple(_corredor(i) for i in ids),
    )


@pytest.fixture
def sem_logging(monkeypatch):
    """O main não deve reconfigurar o logging global durante os testes."""
    monkeypatch.setattr(modulo_main, "setup_logging", lambda **_kwargs: None)


def test_sai_com_codigo_1_quando_a_config_falha(monkeypatch, capsys):
    def falhar(*_args, **_kwargs):
        raise ConfigError("STRAVA_CLIENT_ID não definida")

    monkeypatch.setattr(modulo_main, "load_config", falhar)

    assert modulo_main.main() == 1
    assert "STRAVA_CLIENT_ID" in capsys.readouterr().err


def test_percorre_todos_os_corredores(monkeypatch, sem_logging):
    config = _config("p001", "p002", "p003")
    monkeypatch.setattr(modulo_main, "load_config", lambda *_a, **_k: config)

    visitados: list[str] = []
    monkeypatch.setattr(
        modulo_main,
        "sincronizar_corredor",
        lambda _config, corredor: visitados.append(corredor.id),
    )

    assert modulo_main.main() == 0
    assert visitados == ["p001", "p002", "p003"]


def test_falha_de_um_corredor_nao_interrompe_os_demais(monkeypatch, sem_logging, caplog):
    """Com dezenas de participantes, um token expirado não pode parar a coleta."""
    config = _config("p001", "p002", "p003")

    visitados: list[str] = []

    def sincronizar(_config, corredor):
        visitados.append(corredor.id)
        if corredor.id == "p002":
            raise RuntimeError("token expirado")

    monkeypatch.setattr(modulo_main, "sincronizar_corredor", sincronizar)

    falhas = modulo_main.sincronizar_todos(config)

    assert visitados == ["p001", "p002", "p003"]
    assert falhas == 1
    assert "p002" in caplog.text


def test_codigo_de_saida_1_quando_algum_corredor_falha(monkeypatch, sem_logging):
    """O scheduler precisa enxergar que a execução não foi limpa."""
    config = _config("p001", "p002")
    monkeypatch.setattr(modulo_main, "load_config", lambda *_a, **_k: config)

    def sincronizar(_config, corredor):
        if corredor.id == "p002":
            raise RuntimeError("planilha aberta no Excel")

    monkeypatch.setattr(modulo_main, "sincronizar_corredor", sincronizar)

    assert modulo_main.main() == 1


def test_config_error_nao_escapa_do_main(monkeypatch):
    """O usuário recebe mensagem tratada, não um traceback."""
    def falhar(*_args, **_kwargs):
        raise ConfigError("qualquer coisa")

    monkeypatch.setattr(modulo_main, "load_config", falhar)

    try:
        modulo_main.main()
    except ConfigError:  # pragma: no cover
        pytest.fail("ConfigError vazou do main()")
