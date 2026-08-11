"""Testes do carregamento de configuração (Fase 2)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.utils.config import PROJECT_ROOT, Config, ConfigError, load_config

VARIAVEIS = (
    "STRAVA_CLIENT_ID",
    "STRAVA_CLIENT_SECRET",
    "STRAVA_REFRESH_TOKEN",
    "EXCEL_PATH",
    "TEMPLATE_PATH",
    "START_DATE",
    "LOG_LEVEL",
    "LOG_FILE",
)

OBRIGATORIAS = {
    "STRAVA_CLIENT_ID": "12345",
    "STRAVA_CLIENT_SECRET": "segredo-do-app",
    "STRAVA_REFRESH_TOKEN": "refresh-abcdef",
    "START_DATE": "2026-01-01",
}


@pytest.fixture
def ambiente_limpo(monkeypatch):
    """Remove qualquer configuração herdada do shell — os testes definem a sua."""
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture
def env_inexistente(tmp_path) -> Path:
    """Caminho de `.env` que não existe, para não carregar o `.env` real do repo."""
    return tmp_path / "sem-env.env"


def _configurar(monkeypatch, **valores: str) -> None:
    for nome, valor in valores.items():
        monkeypatch.setenv(nome, valor)


def test_carrega_configuracao_completa(monkeypatch, ambiente_limpo, env_inexistente):
    _configurar(monkeypatch, **OBRIGATORIAS)

    config = load_config(env_file=env_inexistente)

    assert config.strava_client_id == "12345"
    assert config.strava_client_secret == "segredo-do-app"
    assert config.strava_refresh_token == "refresh-abcdef"
    assert config.start_date == date(2026, 1, 1)
    assert config.log_level == "INFO"


def test_caminhos_tem_padrao_e_saem_da_raiz_do_projeto(
    monkeypatch, ambiente_limpo, env_inexistente
):
    _configurar(monkeypatch, **OBRIGATORIAS)

    config = load_config(env_file=env_inexistente)

    assert config.excel_path == PROJECT_ROOT / "data" / "corredor.xlsx"
    assert config.template_path == PROJECT_ROOT / "data" / "template.xlsx"
    assert config.log_file == PROJECT_ROOT / "data" / "stravasync.log"


def test_caminho_relativo_nao_depende_do_diretorio_atual(
    monkeypatch, ambiente_limpo, env_inexistente, tmp_path
):
    """O scheduler (Fase 7) pode rodar de qualquer lugar — o caminho não pode mudar."""
    _configurar(monkeypatch, **OBRIGATORIAS, EXCEL_PATH="./data/joao.xlsx")
    monkeypatch.chdir(tmp_path)

    config = load_config(env_file=env_inexistente)

    assert config.excel_path == PROJECT_ROOT / "data" / "joao.xlsx"


def test_caminho_absoluto_e_preservado(monkeypatch, ambiente_limpo, env_inexistente, tmp_path):
    planilha = tmp_path / "corredor.xlsx"
    _configurar(monkeypatch, **OBRIGATORIAS, EXCEL_PATH=str(planilha))

    config = load_config(env_file=env_inexistente)

    assert config.excel_path == planilha


def test_log_file_vazio_desliga_arquivo(monkeypatch, ambiente_limpo, env_inexistente):
    _configurar(monkeypatch, **OBRIGATORIAS, LOG_FILE="")

    assert load_config(env_file=env_inexistente).log_file is None


def test_le_valores_do_arquivo_env(monkeypatch, ambiente_limpo, tmp_path):
    arquivo = tmp_path / ".env"
    arquivo.write_text(
        "STRAVA_CLIENT_ID=999\n"
        "STRAVA_CLIENT_SECRET=do-arquivo\n"
        "STRAVA_REFRESH_TOKEN=token-do-arquivo\n"
        "START_DATE=2026-03-15\n",
        encoding="utf-8",
    )

    config = load_config(env_file=arquivo)

    assert config.strava_client_id == "999"
    assert config.start_date == date(2026, 3, 15)


def test_ambiente_tem_precedencia_sobre_o_arquivo(monkeypatch, ambiente_limpo, tmp_path):
    """Permite sobrescrever a config no scheduler/CI sem editar o `.env`."""
    arquivo = tmp_path / ".env"
    arquivo.write_text("STRAVA_CLIENT_ID=do-arquivo\n", encoding="utf-8")
    _configurar(monkeypatch, **OBRIGATORIAS)

    assert load_config(env_file=arquivo).strava_client_id == "12345"


def test_erro_lista_todas_as_variaveis_faltantes(monkeypatch, ambiente_limpo, env_inexistente):
    """Uma execução deve revelar tudo que falta, não uma variável por vez."""
    _configurar(monkeypatch, STRAVA_CLIENT_ID="12345")

    with pytest.raises(ConfigError) as erro:
        load_config(env_file=env_inexistente)

    mensagem = str(erro.value)
    assert "STRAVA_CLIENT_SECRET" in mensagem
    assert "STRAVA_REFRESH_TOKEN" in mensagem
    assert "START_DATE" in mensagem
    assert "STRAVA_CLIENT_ID" not in mensagem


def test_variavel_em_branco_conta_como_ausente(monkeypatch, ambiente_limpo, env_inexistente):
    _configurar(monkeypatch, **{**OBRIGATORIAS, "STRAVA_REFRESH_TOKEN": "   "})

    with pytest.raises(ConfigError, match="STRAVA_REFRESH_TOKEN"):
        load_config(env_file=env_inexistente)


def test_start_date_invalida(monkeypatch, ambiente_limpo, env_inexistente):
    _configurar(monkeypatch, **{**OBRIGATORIAS, "START_DATE": "01/01/2026"})

    with pytest.raises(ConfigError, match="YYYY-MM-DD"):
        load_config(env_file=env_inexistente)


def test_log_level_invalido(monkeypatch, ambiente_limpo, env_inexistente):
    _configurar(monkeypatch, **OBRIGATORIAS, LOG_LEVEL="VERBOSO")

    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        load_config(env_file=env_inexistente)


def test_log_level_aceita_minusculas(monkeypatch, ambiente_limpo, env_inexistente):
    _configurar(monkeypatch, **OBRIGATORIAS, LOG_LEVEL="debug")

    assert load_config(env_file=env_inexistente).log_level == "DEBUG"


def test_segredos_nao_aparecem_no_repr():
    config = Config(
        strava_client_id="12345",
        strava_client_secret="nao-me-mostre",
        strava_refresh_token="nem-a-mim",
        excel_path=Path("corredor.xlsx"),
        template_path=Path("template.xlsx"),
        start_date=date(2026, 1, 1),
    )

    texto = repr(config)
    assert "nao-me-mostre" not in texto
    assert "nem-a-mim" not in texto


def test_safe_summary_mascara_o_client_id():
    config = Config(
        strava_client_id="1234567890",
        strava_client_secret="s",
        strava_refresh_token="t",
        excel_path=Path("corredor.xlsx"),
        template_path=Path("template.xlsx"),
        start_date=date(2026, 1, 1),
    )

    resumo = config.safe_summary()
    assert "1234567890" not in resumo
    assert "****7890" in resumo
    assert "2026-01-01" in resumo
