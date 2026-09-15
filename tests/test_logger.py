"""Testes da configuração central de logging (Fase 2)."""
from __future__ import annotations

import logging

import pytest

from src.utils import logger as modulo_logger
from src.utils.logger import get_logger, setup_logging


@pytest.fixture(autouse=True)
def logging_isolado():
    """Salva e restaura o estado global do logging entre os testes."""
    raiz = logging.getLogger()
    handlers_originais = list(raiz.handlers)
    nivel_original = raiz.level
    configurado_original = modulo_logger._configurado

    modulo_logger._configurado = False
    raiz.handlers = []
    try:
        yield raiz
    finally:
        for handler in raiz.handlers:
            handler.close()
        raiz.handlers = handlers_originais
        raiz.setLevel(nivel_original)
        modulo_logger._configurado = configurado_original


def test_configura_console_por_padrao(logging_isolado):
    setup_logging(level="DEBUG")

    assert logging_isolado.level == logging.DEBUG
    assert len(logging_isolado.handlers) == 1


def test_grava_no_arquivo_de_log(logging_isolado, tmp_path):
    arquivo = tmp_path / "logs" / "stravasync.log"

    setup_logging(level="INFO", log_file=arquivo)
    get_logger("teste").info("sincronização concluída")

    for handler in logging_isolado.handlers:
        handler.flush()

    assert arquivo.exists()
    conteudo = arquivo.read_text(encoding="utf-8")
    assert "sincronização concluída" in conteudo  # acentos sobrevivem (encoding utf-8)


def test_cria_o_diretorio_do_log(logging_isolado, tmp_path):
    arquivo = tmp_path / "novo" / "sub" / "app.log"

    setup_logging(log_file=arquivo)

    assert arquivo.parent.is_dir()


def test_e_idempotente(logging_isolado):
    setup_logging()
    setup_logging()
    setup_logging()

    assert len(logging_isolado.handlers) == 1


def test_force_reconfigura_sem_duplicar_handlers(logging_isolado):
    setup_logging(level="INFO")
    setup_logging(level="WARNING", force=True)

    assert len(logging_isolado.handlers) == 1
    assert logging_isolado.level == logging.WARNING


def test_silencia_bibliotecas_barulhentas(logging_isolado):
    setup_logging(level="DEBUG")

    assert logging.getLogger("httpx").level == logging.WARNING


def test_get_logger_devolve_logger_nomeado():
    assert get_logger("src.services.sync_service").name == "src.services.sync_service"


def test_arquivo_de_log_inacessivel_cai_para_console(logging_isolado, tmp_path):
    """Pasta impossível de criar (aqui, um arquivo no lugar do diretório) não
    pode derrubar a inicialização do logging — cai para console apenas."""
    bloqueio = tmp_path / "arquivo.txt"
    bloqueio.write_text("não é um diretório")
    caminho_invalido = bloqueio / "sub" / "app.log"

    setup_logging(log_file=caminho_invalido)  # não deve levantar

    assert len(logging_isolado.handlers) == 1  # só o console; o de arquivo falhou
