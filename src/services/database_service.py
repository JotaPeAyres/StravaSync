"""Infraestrutura do SQLite: conexão e migração do schema.

A Fase 3 cria a tabela de **estado por corredor** (token corrente, atleta
autorizado, última sincronização) — o que o `corredores.toml` não pode guardar
porque o app nunca reescreve aquele arquivo.

O schema evolui por `PRAGMA user_version`, e não por `CREATE TABLE IF NOT
EXISTS` solto: assim a Fase 4 acrescenta a tabela `activities` num bloco novo
sem tocar no que a Fase 3 já gravou nos bancos existentes.

Datas são gravadas como **texto ISO-8601 em UTC**. Os adaptadores automáticos de
`datetime` do `sqlite3` estão depreciados desde o Python 3.12; converter na mão
é explícito e não some numa versão futura.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from src.utils.errors import StateError
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Sobe para 2 na Fase 4, quando entrar a tabela `activities`.
SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS corredor_state (
    corredor_id            TEXT PRIMARY KEY,
    athlete_id             INTEGER,
    scope                  TEXT,
    refresh_token          TEXT,
    access_token           TEXT,
    access_token_expira_em TEXT,
    ultima_sincronizacao   TEXT,
    ultimo_evento_em       TEXT,
    precisa_reinscricao    INTEGER NOT NULL DEFAULT 0,
    motivo_reinscricao     TEXT,
    atualizado_em          TEXT NOT NULL
);
"""

# TODO(Fase 4): schema v2 — tabela `activities`, com `corredor_id` referenciando
# `corredor_state` e o `activity_id` único (dedupe).


class DatabaseService:
    """Gerencia a conexão e o schema do banco local."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        """Abre (ou reaproveita) a conexão com o banco."""
        if self._conn is not None:
            return self._conn

        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.database_path)
        except (OSError, sqlite3.Error) as erro:
            raise StateError(f"não foi possível abrir o banco {self.database_path}: {erro}") from erro

        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self._conn = conn
        return conn

    def init_schema(self) -> None:
        """Aplica as migrações pendentes. Idempotente."""
        conn = self.connect()
        try:
            versao = conn.execute("PRAGMA user_version").fetchone()[0]
            if versao >= SCHEMA_VERSION:
                return

            if versao < 1:
                conn.executescript(_SCHEMA_V1)

            # TODO(Fase 4): if versao < 2: conn.executescript(_SCHEMA_V2)

            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        except sqlite3.Error as erro:
            raise StateError(f"falha ao criar o schema em {self.database_path}: {erro}") from erro

        logger.debug("Schema do banco em %s na versão %d.", self.database_path, SCHEMA_VERSION)

    def close(self) -> None:
        """Fecha a conexão, se houver."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        self.init_schema()
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        erro: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def para_texto(momento: datetime | None) -> str | None:
    """Converte um instante em texto ISO-8601 UTC para gravar no banco."""
    if momento is None:
        return None
    if momento.tzinfo is None:
        # Um datetime ingênuo aqui seria um bug de quem chamou; tratamos como
        # UTC em vez de gravar um instante ambíguo.
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(UTC).isoformat()


def de_texto(valor: str | None) -> datetime | None:
    """Lê um instante gravado por `para_texto`, sempre devolvendo UTC."""
    if not valor:
        return None
    try:
        momento = datetime.fromisoformat(valor)
    except ValueError:
        logger.warning("Data inválida no banco: %r — tratada como ausente.", valor)
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(UTC)
