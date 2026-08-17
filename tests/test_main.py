"""Testes do ponto de entrada (Fase 2): config + logging + código de saída."""
from __future__ import annotations

import pytest

from src import main as modulo_main
from src.utils.config import ConfigError


def test_sai_com_codigo_1_quando_a_config_falha(monkeypatch, capsys):
    """Config inválida aborta antes de qualquer chamada ao Strava."""
    def falhar(*_args, **_kwargs):
        raise ConfigError("STRAVA_CLIENT_ID não definida")

    monkeypatch.setattr(modulo_main, "load_config", falhar)

    assert modulo_main.main() == 1
    assert "STRAVA_CLIENT_ID" in capsys.readouterr().err


def test_sai_com_codigo_0_no_caminho_feliz(monkeypatch, tmp_path):
    """Com config válida, o main liga o logging e conclui."""
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123456")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "segredo")
    monkeypatch.setenv("STRAVA_REFRESH_TOKEN", "token")
    monkeypatch.setenv("START_DATE", "2026-01-01")
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "teste.log"))

    chamadas: list[object] = []
    monkeypatch.setattr(modulo_main, "executar_sincronizacao", chamadas.append)
    monkeypatch.setattr(modulo_main, "setup_logging", lambda **_kwargs: None)

    assert modulo_main.main() == 0
    assert len(chamadas) == 1


def test_config_error_nao_escapa_do_main(monkeypatch):
    """O usuário recebe mensagem tratada, não um traceback."""
    def falhar(*_args, **_kwargs):
        raise ConfigError("qualquer coisa")

    monkeypatch.setattr(modulo_main, "load_config", falhar)

    try:
        modulo_main.main()
    except ConfigError:  # pragma: no cover
        pytest.fail("ConfigError vazou do main()")
