"""Testes da baseline de escrita do Excel (Fase 5.5).

O que este repositório guarda é a única forma de distinguir "o app atualizou
de novo" de "um humano editou a célula" — errar aqui faz o `ExcelService` ou
sobrescrever uma edição humana, ou parar de atualizar um dia legítimo.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.repositories.escrita_excel_repository import EscritaExcelRepository
from src.services.database_service import DatabaseService

AGORA = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def banco(tmp_path):
    servico = DatabaseService(tmp_path / "estado.db")
    servico.init_schema()
    try:
        yield servico
    finally:
        servico.close()


@pytest.fixture
def repo(banco):
    return EscritaExcelRepository(banco.connect(), agora=lambda: AGORA)


def test_carregar_sem_dados_e_vazio(repo):
    assert repo.carregar("p001") == {}


def test_registrar_e_carregar_ida_e_volta(repo):
    repo.registrar_muitas("p001", {date(2026, 1, 1): (10.0, 3000), date(2026, 1, 2): (0.0, 0)})

    assert repo.carregar("p001") == {
        date(2026, 1, 1): (10.0, 3000),
        date(2026, 1, 2): (0.0, 0),
    }


def test_registrar_de_novo_atualiza_em_vez_de_duplicar(repo):
    repo.registrar_muitas("p001", {date(2026, 1, 1): (10.0, 3000)})
    repo.registrar_muitas("p001", {date(2026, 1, 1): (12.0, 3600)})

    assert repo.carregar("p001") == {date(2026, 1, 1): (12.0, 3600)}


def test_baseline_e_por_corredor(repo):
    repo.registrar_muitas("p001", {date(2026, 1, 1): (10.0, 3000)})

    assert repo.carregar("p002") == {}


def test_registrar_vazio_nao_toca_o_banco(repo, banco):
    repo.registrar_muitas("p001", {})

    linha = banco.connect().execute("SELECT COUNT(*) AS n FROM excel_escritas").fetchone()
    assert linha["n"] == 0
