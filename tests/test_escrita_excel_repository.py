"""Testes da baseline de escrita do Excel (Fase 5.5).

O que este repositório guarda é a única forma de distinguir "o app atualizou
de novo" de "um humano editou a célula" — errar aqui faz o `ExcelService` ou
sobrescrever uma edição humana, ou parar de atualizar um dia legítimo.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

import pytest

from src.repositories.escrita_excel_repository import EscritaExcelRepository
from src.services.database_service import DatabaseService
from src.utils.errors import StateError

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


def test_carregar_isola_corredores_com_o_mesmo_dia(repo):
    """Dois corredores podem escrever no mesmo dia-calendário sem se misturar."""
    repo.registrar_muitas("p001", {date(2026, 1, 1): (10.0, 3000)})
    repo.registrar_muitas("p002", {date(2026, 1, 1): (5.0, 1500)})

    assert repo.carregar("p001") == {date(2026, 1, 1): (10.0, 3000)}
    assert repo.carregar("p002") == {date(2026, 1, 1): (5.0, 1500)}


# --------------------------------------------------------------------- erros


def test_carregar_propaga_stateerror_quando_a_tabela_nao_existe(tmp_path):
    conn = sqlite3.connect(tmp_path / "vazio.db")
    conn.row_factory = sqlite3.Row
    try:
        repo = EscritaExcelRepository(conn, agora=lambda: AGORA)
        with pytest.raises(StateError):
            repo.carregar("p001")
    finally:
        conn.close()


class _ConexaoComExecutemanyQuebrado(sqlite3.Connection):
    """`sqlite3.Connection` é um tipo C imutável — não aceita monkeypatch de
    método nem na classe nem na instância. Uma subclasse é o jeito de simular
    uma falha real do driver sem tocar o `sqlite3` global."""

    def executemany(self, *args, **kwargs):
        raise sqlite3.OperationalError("falha simulada")


def test_registrar_muitas_propaga_stateerror_sem_gravacao_parcial(tmp_path):
    caminho = tmp_path / "estado.db"
    banco = DatabaseService(caminho)
    banco.init_schema()
    banco.close()

    conn = sqlite3.connect(caminho, factory=_ConexaoComExecutemanyQuebrado)
    conn.row_factory = sqlite3.Row
    repo = EscritaExcelRepository(conn, agora=lambda: AGORA)

    try:
        with pytest.raises(StateError):
            repo.registrar_muitas("p001", {date(2026, 1, 1): (10.0, 3000)})
    finally:
        conn.close()

    verificacao = sqlite3.connect(caminho)
    try:
        linha = verificacao.execute("SELECT COUNT(*) AS n FROM excel_escritas").fetchone()
        assert linha[0] == 0
    finally:
        verificacao.close()


def test_duas_conexoes_escrevendo_o_mesmo_dia_nao_corrompe(tmp_path):
    """Simula duas execuções concorrentes gravando a mesma baseline."""
    caminho = tmp_path / "estado.db"
    banco1 = DatabaseService(caminho)
    banco1.init_schema()
    banco2 = DatabaseService(caminho)

    try:
        repo1 = EscritaExcelRepository(banco1.connect(), agora=lambda: AGORA)
        repo2 = EscritaExcelRepository(banco2.connect(), agora=lambda: AGORA)

        repo1.registrar_muitas("p001", {date(2026, 1, 1): (10.0, 3000)})
        repo2.registrar_muitas("p001", {date(2026, 1, 1): (12.0, 3600)})

        assert repo1.carregar("p001") == {date(2026, 1, 1): (12.0, 3600)}
    finally:
        banco1.close()
        banco2.close()
