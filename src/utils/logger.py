"""Configuração central de logging.

`setup_logging()` é chamada uma única vez no início da execução (`main.py`);
o resto do código só pede loggers com `get_logger(__name__)`.

Este módulo não importa `config` de propósito: recebe valores primitivos, para
poder ser usado em testes e scripts sem um `.env` válido.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMATO = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
FORMATO_DATA = "%Y-%m-%d %H:%M:%S"

TAMANHO_MAXIMO_LOG = 1_000_000  # ~1 MB por arquivo
ARQUIVOS_DE_BACKUP = 5

# Bibliotecas de terceiros só aparecem no log a partir de WARNING — em DEBUG o
# httpx registra cada requisição e afoga as mensagens da aplicação.
_LOGGERS_BARULHENTOS = ("httpx", "httpcore", "urllib3")

_configurado = False


def setup_logging(
    level: int | str = logging.INFO,
    log_file: Path | None = None,
    *,
    force: bool = False,
) -> None:
    """Configura o logger raiz com saída no console e, opcionalmente, em arquivo.

    Idempotente: chamadas seguintes são ignoradas, a menos que `force=True`
    (útil em testes, para reconfigurar sem herdar os handlers anteriores).

    Args:
        level: nível mínimo, como int (`logging.DEBUG`) ou nome (`"DEBUG"`).
        log_file: arquivo de log rotativo; `None` mantém apenas o console.
        force: recria os handlers mesmo que o logging já tenha sido configurado.
    """
    global _configurado
    if _configurado and not force:
        return

    raiz = logging.getLogger()
    for handler in list(raiz.handlers):
        raiz.removeHandler(handler)
        handler.close()

    raiz.setLevel(level)
    formatador = logging.Formatter(FORMATO, datefmt=FORMATO_DATA)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatador)
    raiz.addHandler(console)

    if log_file is not None:
        arquivo = _handler_de_arquivo(log_file, formatador)
        if arquivo is not None:
            raiz.addHandler(arquivo)

    for nome in _LOGGERS_BARULHENTOS:
        logging.getLogger(nome).setLevel(logging.WARNING)

    _configurado = True


def get_logger(name: str) -> logging.Logger:
    """Retorna um logger nomeado (use `__name__` no módulo chamador)."""
    return logging.getLogger(name)


def _handler_de_arquivo(log_file: Path, formatador: logging.Formatter) -> logging.Handler | None:
    """Cria o handler rotativo, ou `None` se o arquivo não for gravável.

    Um log inacessível (pasta somente-leitura, disco cheio) não pode derrubar a
    sincronização: nesse caso avisamos pelo console — que já está configurado
    neste ponto — e seguimos sem arquivo.
    """
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_file,
            maxBytes=TAMANHO_MAXIMO_LOG,
            backupCount=ARQUIVOS_DE_BACKUP,
            encoding="utf-8",  # mensagens em português têm acentos
        )
        handler.setFormatter(formatador)
        return handler
    except OSError as erro:
        logging.getLogger(__name__).warning(
            "Não foi possível abrir o arquivo de log %s (%s); usando apenas o console.",
            log_file,
            erro,
        )
        return None
