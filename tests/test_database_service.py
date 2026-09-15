"""Testes do `DatabaseService` (Fase 8): conexão, ciclo de vida e migração.

Antes da Fase 8 este módulo só era exercitado incidentalmente — como fixture
de outros testes, ou dentro de `test_activity_repository.py`. Aqui é o
serviço em si: `connect`/`init_schema`/`close`, a migração aditiva por
`PRAGMA user_version` e as conversões de data (`de_texto`/`para_texto` e as
variantes `_local`).
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.services import database_service as modulo_db
from src.services.database_service import (
    DatabaseService,
    de_texto,
    de_texto_local,
    para_texto,
    para_texto_local,
)
from src.utils.errors import StateError

# --------------------------------------------------------------- connect()


def test_connect_cria_o_diretorio_pai_se_nao_existir(tmp_path):
    caminho = tmp_path / "sub" / "dir" / "banco.db"
    servico = DatabaseService(caminho)
    try:
        servico.connect()
        assert caminho.parent.is_dir()
    finally:
        servico.close()


def test_connect_reaproveita_a_mesma_conexao(tmp_path):
    servico = DatabaseService(tmp_path / "banco.db")
    try:
        assert servico.connect() is servico.connect()
    finally:
        servico.close()


def test_connect_liga_pragma_foreign_keys(tmp_path):
    servico = DatabaseService(tmp_path / "banco.db")
    try:
        conn = servico.connect()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        servico.close()


def test_connect_propaga_stateerror_quando_o_caminho_e_invalido(tmp_path):
    bloqueio = tmp_path / "arquivo.txt"
    bloqueio.write_text("não é um diretório")
    caminho_invalido = bloqueio / "sub" / "banco.db"

    with pytest.raises(StateError):
        DatabaseService(caminho_invalido).connect()


# ------------------------------------------------------------ init_schema()


def test_init_schema_e_idempotente(tmp_path):
    servico = DatabaseService(tmp_path / "banco.db")
    try:
        servico.init_schema()
        servico.init_schema()  # não deve levantar nem duplicar nada

        versao = servico.connect().execute("PRAGMA user_version").fetchone()[0]
        assert versao == modulo_db.SCHEMA_VERSION
    finally:
        servico.close()


def test_init_schema_cria_tabelas_e_indices(tmp_path):
    servico = DatabaseService(tmp_path / "banco.db")
    try:
        servico.init_schema()
        conn = servico.connect()
        nomes = {
            linha[0]
            for linha in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            ).fetchall()
        }
        for esperado in (
            "corredor_state",
            "activities",
            "excel_escritas",
            "ix_activities_corredor_day",
            "ux_activities_corredor_day_manual",
        ):
            assert esperado in nomes, f"{esperado} não foi criado"
    finally:
        servico.close()


def test_init_schema_propaga_stateerror_em_banco_corrompido(tmp_path):
    caminho = tmp_path / "corrompido.db"
    caminho.write_bytes(b"isto nao e um arquivo sqlite valido")

    with pytest.raises(StateError):
        DatabaseService(caminho).init_schema()


def _semear_banco_legado(caminho: Path, *, versao: int) -> None:
    """Cria um banco só com as tabelas/colunas que existiam na `versao` dada.

    Reproduz o que um banco real de produção teria antes de uma migração —
    diferente de forçar `SCHEMA_VERSION` via monkeypatch, que não teria
    pego a armadilha documentada no CLAUDE.md (Fase 7): os blocos `if versao
    < N:` comparam contra números literais, não contra a constante, então um
    monkeypatch aplicaria todas as migrações de qualquer forma.
    """
    conn = sqlite3.connect(caminho)
    try:
        conn.executescript(modulo_db._SCHEMA_V1)
        if versao >= 2:
            conn.executescript(modulo_db._SCHEMA_V2)
        if versao >= 3:
            conn.executescript(modulo_db._SCHEMA_V3)
        conn.execute(
            "INSERT INTO corredor_state (corredor_id, atualizado_em) VALUES (?, ?)",
            ("p001", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(f"PRAGMA user_version = {versao}")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("versao_inicial", [1, 2, 3])
def test_migracao_aditiva_preserva_dados_existentes(tmp_path, versao_inicial):
    caminho = tmp_path / "legado.db"
    _semear_banco_legado(caminho, versao=versao_inicial)

    servico = DatabaseService(caminho)
    try:
        servico.init_schema()
        conn = servico.connect()

        linha = conn.execute(
            "SELECT corredor_id, falhas_consecutivas FROM corredor_state WHERE corredor_id = ?",
            ("p001",),
        ).fetchone()
        assert linha[0] == "p001"
        assert linha[1] == 0  # coluna nova (v4), com o default

        assert conn.execute("PRAGMA user_version").fetchone()[0] == modulo_db.SCHEMA_VERSION
    finally:
        servico.close()


# ------------------------------------------------------------------- close()


def test_close_fecha_e_permite_reabrir(tmp_path):
    servico = DatabaseService(tmp_path / "banco.db")
    primeira = servico.connect()
    servico.close()

    segunda = servico.connect()
    try:
        assert segunda is not primeira
        segunda.execute("SELECT 1")
    finally:
        servico.close()


def test_close_sem_conexao_aberta_nao_levanta(tmp_path):
    servico = DatabaseService(tmp_path / "banco.db")
    servico.close()  # não deve levantar


def test_context_manager_chama_init_schema_e_close(tmp_path):
    caminho = tmp_path / "banco.db"
    with DatabaseService(caminho) as servico:
        conn = servico.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == modulo_db.SCHEMA_VERSION

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


# ------------------------------------------------------- para_texto / de_texto


def test_para_texto_none_retorna_none():
    assert para_texto(None) is None


def test_para_texto_assume_utc_quando_ingenuo():
    momento = datetime(2026, 1, 1, 10, 0)  # noqa: DTZ001 — ingênuo de propósito
    assert para_texto(momento) == "2026-01-01T10:00:00+00:00"


def test_para_texto_normaliza_fuso_nao_utc_para_utc():
    momento = datetime(2026, 1, 1, 10, 0, tzinfo=timezone(timedelta(hours=-3)))
    assert para_texto(momento) == "2026-01-01T13:00:00+00:00"


def test_de_texto_none_e_vazio_retornam_none():
    assert de_texto(None) is None
    assert de_texto("") is None


def test_de_texto_ida_e_volta_com_para_texto():
    momento = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert de_texto(para_texto(momento)) == momento


def test_de_texto_valor_invalido_retorna_none_e_loga_warning(caplog):
    with caplog.at_level("WARNING"):
        resultado = de_texto("isto-nao-e-uma-data")

    assert resultado is None
    assert "inválida" in caplog.text


# ----------------------------------------------------- *_local variantes


def test_para_texto_local_preserva_horario_ingenuo():
    momento = datetime(2026, 1, 1, 22, 10)  # noqa: DTZ001 — ingênuo de propósito
    assert para_texto_local(momento) == "2026-01-01T22:10:00"


def test_para_texto_local_descarta_fuso_com_log_debug(caplog):
    momento = datetime(2026, 1, 1, 22, 10, tzinfo=UTC)

    with caplog.at_level("DEBUG"):
        texto = para_texto_local(momento)

    assert texto == "2026-01-01T22:10:00"
    assert "descartado" in caplog.text


def test_de_texto_local_ida_e_volta():
    momento = datetime(2026, 1, 1, 22, 10)  # noqa: DTZ001 — ingênuo de propósito
    assert de_texto_local(para_texto_local(momento)) == momento


def test_de_texto_local_invalido_levanta_stateerror():
    with pytest.raises(StateError):
        de_texto_local("isto-nao-e-uma-data")
