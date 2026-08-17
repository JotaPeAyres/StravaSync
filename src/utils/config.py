"""Carrega a configuração a partir do ambiente (.env).

Campos: credenciais do Strava, caminhos do Excel/template e a data de início
(Dia 1) da planilha.

O objeto `Config` é imutável e validado na criação: se algo obrigatório estiver
faltando ou mal formatado, `load_config()` levanta `ConfigError` listando
**todos** os problemas de uma vez, para o usuário corrigir o `.env` numa única
passada.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# Raiz do repositório (src/utils/config.py -> src/utils -> src -> raiz).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Caminhos relativos no .env são resolvidos a partir da raiz do projeto, e não do
# diretório de trabalho — o scheduler (Fase 7) pode executar de qualquer lugar.
DEFAULT_EXCEL_PATH = "./data/corredor.xlsx"
# O template versionado fica na raiz do repo; só a cópia do corredor vai para
# data/ (que é ignorada pelo git).
DEFAULT_TEMPLATE_PATH = "./Cópia de Planilha_carga_corrida.xlsx"
DEFAULT_LOG_FILE = "./data/stravasync.log"
DEFAULT_LOG_LEVEL = "INFO"

_NIVEIS_DE_LOG = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


class ConfigError(RuntimeError):
    """Configuração ausente ou inválida — o app não tem como seguir."""


@dataclass(frozen=True)
class Config:
    """Configuração da instância (um corredor por instância)."""

    # Segredos ficam fora do repr para não vazarem em log ou traceback.
    strava_client_id: str = field(repr=False)
    strava_client_secret: str = field(repr=False)
    strava_refresh_token: str = field(repr=False)

    excel_path: Path
    template_path: Path
    start_date: date
    cutover_date: date

    log_level: str = DEFAULT_LOG_LEVEL
    log_file: Path | None = None

    @property
    def adota_planilha_existente(self) -> bool:
        """True quando há histórico manual anterior ao corte, a ser preservado."""
        return self.cutover_date > self.start_date

    def safe_summary(self) -> str:
        """Resumo de uma linha para o log de inicialização, com segredos mascarados."""
        return (
            f"client_id={_mascarar(self.strava_client_id)} "
            f"excel={self.excel_path} "
            f"template={self.template_path} "
            f"start_date={self.start_date.isoformat()} "
            f"cutover={self.cutover_date.isoformat()} "
            f"log_level={self.log_level} "
            f"log_file={self.log_file or '(desativado)'}"
        )


def load_config(env_file: Path | None = None) -> Config:
    """Lê o `.env` (se existir) e o ambiente, devolvendo um `Config` validado.

    Variáveis já presentes no ambiente têm precedência sobre o `.env`, o que
    permite sobrescrever a configuração no scheduler ou em CI sem editar arquivo.

    Raises:
        ConfigError: se faltar variável obrigatória ou algum valor for inválido.
    """
    load_dotenv(env_file if env_file is not None else PROJECT_ROOT / ".env")

    erros: list[str] = []

    client_id = _obrigatorio("STRAVA_CLIENT_ID", erros)
    client_secret = _obrigatorio("STRAVA_CLIENT_SECRET", erros)
    refresh_token = _obrigatorio("STRAVA_REFRESH_TOKEN", erros)
    start_date = _data("START_DATE", erros)
    # Sem CUTOVER_DATE o app assume a planilha desde o Dia 1 (corredor novo).
    cutover_date = _data_opcional("CUTOVER_DATE", start_date, erros)
    if cutover_date < start_date:
        erros.append(
            f"CUTOVER_DATE={cutover_date.isoformat()} é anterior a "
            f"START_DATE={start_date.isoformat()} (o corte não pode ficar fora da planilha)"
        )
    log_level = _nivel_de_log("LOG_LEVEL", erros)

    excel_path = _caminho(os.getenv("EXCEL_PATH") or DEFAULT_EXCEL_PATH)
    template_path = _caminho(os.getenv("TEMPLATE_PATH") or DEFAULT_TEMPLATE_PATH)

    # LOG_FILE vazio (`LOG_FILE=`) desliga o arquivo de log, mantendo só o console.
    log_file_bruto = os.getenv("LOG_FILE", DEFAULT_LOG_FILE).strip()
    log_file = _caminho(log_file_bruto) if log_file_bruto else None

    if erros:
        raise ConfigError(
            "Configuração inválida:\n  - "
            + "\n  - ".join(erros)
            + "\n\nCopie o `.env.example` para `.env` e preencha os valores."
        )

    return Config(
        strava_client_id=client_id,
        strava_client_secret=client_secret,
        strava_refresh_token=refresh_token,
        excel_path=excel_path,
        template_path=template_path,
        start_date=start_date,
        cutover_date=cutover_date,
        log_level=log_level,
        log_file=log_file,
    )


def _obrigatorio(nome: str, erros: list[str]) -> str:
    """Devolve a variável de ambiente `nome`, acumulando erro se estiver vazia."""
    valor = (os.getenv(nome) or "").strip()
    if not valor:
        erros.append(f"{nome} não definida")
    return valor


def _data(nome: str, erros: list[str]) -> date:
    """Converte a variável `nome` para `date` (YYYY-MM-DD), acumulando erros."""
    valor = _obrigatorio(nome, erros)
    if not valor:
        return date.min
    try:
        return date.fromisoformat(valor)
    except ValueError:
        erros.append(f"{nome}={valor!r} não é uma data no formato YYYY-MM-DD")
        return date.min


def _data_opcional(nome: str, padrao: date, erros: list[str]) -> date:
    """Como `_data`, mas cai no `padrao` quando a variável não é definida."""
    valor = (os.getenv(nome) or "").strip()
    if not valor:
        return padrao
    try:
        return date.fromisoformat(valor)
    except ValueError:
        erros.append(f"{nome}={valor!r} não é uma data no formato YYYY-MM-DD")
        return padrao


def _nivel_de_log(nome: str, erros: list[str]) -> str:
    """Valida o nível de log, caindo no padrão quando a variável não é definida."""
    valor = (os.getenv(nome) or DEFAULT_LOG_LEVEL).strip().upper()
    if valor not in _NIVEIS_DE_LOG:
        erros.append(f"{nome}={valor!r} inválido (use um de: {', '.join(sorted(_NIVEIS_DE_LOG))})")
        return DEFAULT_LOG_LEVEL
    return valor


def _caminho(valor: str) -> Path:
    """Resolve um caminho do `.env`; relativos partem da raiz do projeto."""
    caminho = Path(valor.strip()).expanduser()
    if not caminho.is_absolute():
        caminho = PROJECT_ROOT / caminho
    return Path(os.path.normpath(caminho))


def _mascarar(segredo: str) -> str:
    """Mostra apenas os últimos 4 caracteres de um segredo."""
    if len(segredo) <= 4:
        return "****"
    return f"****{segredo[-4:]}"
