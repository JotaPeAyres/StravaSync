"""Testes do carregamento de configuração (Fase 2, multi-corredor)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.models.corredor import Corredor
from src.utils.config import PROJECT_ROOT, Config, ConfigError, load_config

VARIAVEIS = (
    "STRAVA_CLIENT_ID",
    "STRAVA_CLIENT_SECRET",
    "CORREDORES_PATH",
    "TEMPLATE_PATH",
    "DATABASE_PATH",
    "LOG_LEVEL",
    "LOG_FILE",
)

CREDENCIAIS = {
    "STRAVA_CLIENT_ID": "12345",
    "STRAVA_CLIENT_SECRET": "segredo-do-app",
}

CADASTRO_MINIMO = """
[[corredor]]
id = "p001"
refresh_token = "token-p001"
start_date = 2026-01-01
"""


@pytest.fixture
def ambiente_limpo(monkeypatch):
    """Remove qualquer configuração herdada do shell — os testes definem a sua."""
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)


@pytest.fixture
def env_inexistente(tmp_path) -> Path:
    """Caminho de `.env` que não existe, para não carregar o `.env` real do repo."""
    return tmp_path / "sem-env.env"


def _cadastro(tmp_path: Path, conteudo: str) -> Path:
    arquivo = tmp_path / "corredores.toml"
    arquivo.write_text(conteudo, encoding="utf-8")
    return arquivo


def _carregar(monkeypatch, tmp_path, conteudo: str = CADASTRO_MINIMO, **env: str) -> Config:
    for nome, valor in {**CREDENCIAIS, **env}.items():
        monkeypatch.setenv(nome, valor)
    return load_config(
        env_file=tmp_path / "sem-env.env",
        corredores_file=_cadastro(tmp_path, conteudo),
    )


def test_carrega_credenciais_da_aplicacao(monkeypatch, ambiente_limpo, tmp_path):
    """CLIENT_ID/SECRET são do app registrado, comuns a todos os participantes."""
    config = _carregar(monkeypatch, tmp_path)

    assert config.strava_client_id == "12345"
    assert config.strava_client_secret == "segredo-do-app"
    assert config.log_level == "INFO"


def test_carrega_varios_corredores(monkeypatch, ambiente_limpo, tmp_path):
    config = _carregar(
        monkeypatch,
        tmp_path,
        """
        [[corredor]]
        id = "p001"
        nome = "Ana"
        refresh_token = "token-1"
        start_date = 2026-01-01

        [[corredor]]
        id = "p002"
        refresh_token = "token-2"
        start_date = 2026-02-15
        """,
    )

    assert len(config.corredores) == 2
    ana, segundo = config.corredores
    assert ana.nome == "Ana"
    assert ana.start_date == date(2026, 1, 1)
    assert segundo.nome == "p002"  # sem nome, cai no id
    assert segundo.start_date == date(2026, 2, 15)


def test_cada_corredor_tem_sua_data_de_entrada(monkeypatch, ambiente_limpo, tmp_path):
    """Participantes entram na pesquisa em datas diferentes."""
    config = _carregar(
        monkeypatch,
        tmp_path,
        """
        [[corredor]]
        id = "p001"
        refresh_token = "t1"
        start_date = 2026-01-01

        [[corredor]]
        id = "p002"
        refresh_token = "t2"
        start_date = 2026-06-20
        """,
    )

    datas = {c.id: c.start_date for c in config.corredores}
    assert datas == {"p001": date(2026, 1, 1), "p002": date(2026, 6, 20)}


def test_planilha_padrao_deriva_do_id(monkeypatch, ambiente_limpo, tmp_path):
    config = _carregar(monkeypatch, tmp_path)

    assert config.corredores[0].excel_path == PROJECT_ROOT / "data" / "p001.xlsx"


def test_planilha_explicita_sai_da_raiz_do_projeto(monkeypatch, ambiente_limpo, tmp_path):
    config = _carregar(
        monkeypatch,
        tmp_path,
        """
        [[corredor]]
        id = "p001"
        refresh_token = "t"
        start_date = 2026-01-01
        excel_path = "./data/ana.xlsx"
        """,
    )

    assert config.corredores[0].excel_path == PROJECT_ROOT / "data" / "ana.xlsx"


def test_cutover_padrao_e_a_propria_start_date(monkeypatch, ambiente_limpo, tmp_path):
    corredor = _carregar(monkeypatch, tmp_path).corredores[0]

    assert corredor.cutover_date == corredor.start_date
    assert corredor.adota_planilha_existente is False


def test_cutover_posterior_marca_adocao(monkeypatch, ambiente_limpo, tmp_path):
    """Planilha preenchida à mão: tudo antes do corte é território do corredor."""
    corredor = _carregar(
        monkeypatch,
        tmp_path,
        """
        [[corredor]]
        id = "p003"
        refresh_token = "t"
        start_date = 2025-11-01
        cutover_date = 2026-03-01
        """,
    ).corredores[0]

    assert corredor.adota_planilha_existente is True
    assert corredor.cutover_date == date(2026, 3, 1)


def test_cutover_anterior_ao_dia_1_e_erro(monkeypatch, ambiente_limpo, tmp_path):
    with pytest.raises(ConfigError, match="cutover_date"):
        _carregar(
            monkeypatch,
            tmp_path,
            """
            [[corredor]]
            id = "p001"
            refresh_token = "t"
            start_date = 2026-01-01
            cutover_date = 2025-12-31
            """,
        )


def test_ids_repetidos_sao_erro(monkeypatch, ambiente_limpo, tmp_path):
    """Id repetido faria um participante sobrescrever o outro."""
    with pytest.raises(ConfigError, match="id repetido"):
        _carregar(
            monkeypatch,
            tmp_path,
            """
            [[corredor]]
            id = "p001"
            refresh_token = "t1"
            start_date = 2026-01-01

            [[corredor]]
            id = "p001"
            refresh_token = "t2"
            start_date = 2026-02-01
            """,
        )


def test_planilhas_repetidas_sao_erro(monkeypatch, ambiente_limpo, tmp_path):
    """Dois corredores na mesma planilha corromperiam os dados dos dois."""
    with pytest.raises(ConfigError, match="excel_path repetido"):
        _carregar(
            monkeypatch,
            tmp_path,
            """
            [[corredor]]
            id = "p001"
            refresh_token = "t1"
            start_date = 2026-01-01
            excel_path = "./data/compartilhada.xlsx"

            [[corredor]]
            id = "p002"
            refresh_token = "t2"
            start_date = 2026-02-01
            excel_path = "./data/compartilhada.xlsx"
            """,
        )


def test_campo_desconhecido_e_erro(monkeypatch, ambiente_limpo, tmp_path):
    """`data_inicio` em vez de `start_date` viraria participante sem histórico."""
    with pytest.raises(ConfigError, match="não reconhecido"):
        _carregar(
            monkeypatch,
            tmp_path,
            """
            [[corredor]]
            id = "p001"
            refresh_token = "t"
            start_date = 2026-01-01
            data_inicio = 2026-01-01
            """,
        )


def test_erros_de_varios_corredores_saem_juntos(monkeypatch, ambiente_limpo, tmp_path):
    """Com dezenas de participantes, corrigir um erro por execução é inviável."""
    with pytest.raises(ConfigError) as erro:
        _carregar(
            monkeypatch,
            tmp_path,
            """
            [[corredor]]
            id = "p001"
            start_date = 2026-01-01

            [[corredor]]
            id = "p002"
            refresh_token = "t2"

            [[corredor]]
            refresh_token = "t3"
            start_date = 2026-01-01
            """,
        )

    mensagem = str(erro.value)
    assert "'p001'" in mensagem and "refresh_token" in mensagem
    assert "'p002'" in mensagem and "start_date" in mensagem
    assert "corredor[3]" in mensagem and "`id` é obrigatório" in mensagem


def test_start_date_invalida(monkeypatch, ambiente_limpo, tmp_path):
    with pytest.raises(ConfigError, match="YYYY-MM-DD"):
        _carregar(
            monkeypatch,
            tmp_path,
            """
            [[corredor]]
            id = "p001"
            refresh_token = "t"
            start_date = "01/01/2026"
            """,
        )


def test_cadastro_inexistente(monkeypatch, ambiente_limpo, tmp_path, env_inexistente):
    for nome, valor in CREDENCIAIS.items():
        monkeypatch.setenv(nome, valor)

    with pytest.raises(ConfigError, match="cadastro de corredores não encontrado"):
        load_config(env_file=env_inexistente, corredores_file=tmp_path / "nao-existe.toml")


def test_cadastro_vazio(monkeypatch, ambiente_limpo, tmp_path):
    with pytest.raises(ConfigError, match="nenhum corredor cadastrado"):
        _carregar(monkeypatch, tmp_path, "# sem participantes\n")


def test_cadastro_com_toml_invalido(monkeypatch, ambiente_limpo, tmp_path):
    with pytest.raises(ConfigError, match="não é um TOML válido"):
        _carregar(monkeypatch, tmp_path, "[[corredor]\nid = ")


def test_credenciais_da_aplicacao_sao_obrigatorias(monkeypatch, ambiente_limpo, tmp_path):
    with pytest.raises(ConfigError) as erro:
        load_config(
            env_file=tmp_path / "sem-env.env",
            corredores_file=_cadastro(tmp_path, CADASTRO_MINIMO),
        )

    mensagem = str(erro.value)
    assert "STRAVA_CLIENT_ID" in mensagem
    assert "STRAVA_CLIENT_SECRET" in mensagem


def test_caminhos_globais_tem_padrao(monkeypatch, ambiente_limpo, tmp_path):
    config = _carregar(monkeypatch, tmp_path)

    assert config.database_path == PROJECT_ROOT / "data" / "stravasync.db"
    assert config.log_file == PROJECT_ROOT / "data" / "stravasync.log"
    # O template padrão é o arquivo versionado na raiz — precisa existir de fato.
    assert config.template_path == PROJECT_ROOT / "Cópia de Planilha_carga_corrida.xlsx"
    assert config.template_path.is_file()


def test_caminho_nao_depende_do_diretorio_atual(monkeypatch, ambiente_limpo, tmp_path):
    """O scheduler (Fase 7) pode rodar de qualquer lugar."""
    monkeypatch.chdir(tmp_path)

    config = _carregar(monkeypatch, tmp_path, DATABASE_PATH="./data/pesquisa.db")

    assert config.database_path == PROJECT_ROOT / "data" / "pesquisa.db"


def test_log_file_vazio_desliga_arquivo(monkeypatch, ambiente_limpo, tmp_path):
    assert _carregar(monkeypatch, tmp_path, LOG_FILE="").log_file is None


def test_log_level_invalido(monkeypatch, ambiente_limpo, tmp_path):
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        _carregar(monkeypatch, tmp_path, LOG_LEVEL="VERBOSO")


def test_log_level_aceita_minusculas(monkeypatch, ambiente_limpo, tmp_path):
    assert _carregar(monkeypatch, tmp_path, LOG_LEVEL="debug").log_level == "DEBUG"


def test_segredos_nao_aparecem_no_repr(monkeypatch, ambiente_limpo, tmp_path):
    config = _carregar(monkeypatch, tmp_path)

    texto = repr(config)
    assert "segredo-do-app" not in texto
    assert "token-p001" not in texto
    assert "token-p001" not in repr(config.corredores[0])


def test_safe_summary_mascara_e_conta_corredores(monkeypatch, ambiente_limpo, tmp_path):
    config = _carregar(monkeypatch, tmp_path, STRAVA_CLIENT_ID="1234567890")

    resumo = config.safe_summary()
    assert "1234567890" not in resumo
    assert "****7890" in resumo
    assert "corredores=1" in resumo


def test_corredor_mapeia_data_para_dia():
    corredor = Corredor(
        id="p001",
        nome="Ana",
        refresh_token="t",
        start_date=date(2026, 1, 1),
        cutover_date=date(2026, 1, 1),
        excel_path=Path("p001.xlsx"),
    )

    assert corredor.dia_da_planilha(date(2026, 1, 1)) == 1
    assert corredor.dia_da_planilha(date(2026, 1, 10)) == 10
    # Anterior à entrada na pesquisa: não tem linha na planilha.
    assert corredor.dia_da_planilha(date(2025, 12, 31)) == 0
