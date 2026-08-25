"""Infraestrutura do SQLite: conexão e migração do schema.

Duas tabelas:

- `corredor_state` (v1) — o estado do OAuth por participante, que o
  `corredores.toml` não pode guardar porque o app nunca reescreve aquele arquivo.
- `activities` (v2) — os dados ricos por atividade, que não vão para o Excel.

O schema evolui por `PRAGMA user_version`, e não por `CREATE TABLE IF NOT
EXISTS` solto: cada versão acrescenta um bloco sem tocar no que a anterior
gravou nos bancos que já existem.

Datas são gravadas como **texto**, nunca como objeto: os adaptadores automáticos
de `datetime` do `sqlite3` estão depreciados desde o Python 3.12. E são **dois
pares de conversores**, porque o projeto lida com dois tipos de instante que não
podem ser confundidos:

- `para_texto` / `de_texto` — instantes **absolutos**, normalizados para UTC.
  São o `start_date` do Strava e os carimbos de execução.
- `para_texto_local` / `de_texto_local` — horário **local e ingênuo** do
  corredor (o `start_date_local`). Passar um desses pelo par de cima gravaria
  "22h UTC" para uma corrida das 22h locais e devolveria um datetime *aware*,
  deslocando a corrida de dia — ou seja, de linha da planilha.
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

SCHEMA_VERSION = 2

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

# `activities` guarda os dados ricos por atividade — o que NÃO vai para o Excel.
#
# `corredor_id` se relaciona logicamente com `corredor_state`, mas **sem FOREIGN
# KEY declarada**: `corredor_state` é cache do estado do OAuth, não o cadastro da
# pesquisa (quem existe é o `corredores.toml`). Um participante pode ter dados
# sem nunca ter se inscrito — é exatamente o caso da adoção de planilha
# preenchida à mão (Fase 5.5), em que a planilha existe há meses e o OAuth é de
# hoje. Uma FK transformaria isso em erro, e `ON DELETE CASCADE` apagaria dados
# de pesquisa junto com um token. O índice abaixo dá a performance que se
# esperaria da FK.
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS activities (
    id                INTEGER PRIMARY KEY,
    corredor_id       TEXT    NOT NULL,
    activity_id       INTEGER,
    origem            TEXT    NOT NULL DEFAULT 'strava',
    name              TEXT    NOT NULL DEFAULT '',
    type              TEXT    NOT NULL DEFAULT 'Run',
    day               TEXT    NOT NULL,
    date_local        TEXT    NOT NULL,
    start_date_utc    TEXT,
    distance_m        REAL    NOT NULL,
    moving_time_s     INTEGER NOT NULL,
    elapsed_time_s    INTEGER NOT NULL,
    average_speed     REAL,
    average_heartrate REAL,
    max_heartrate     REAL,
    elevation_gain    REAL,
    calories          REAL,
    cadence           REAL,
    criado_em         TEXT    NOT NULL,
    atualizado_em     TEXT    NOT NULL,
    UNIQUE (corredor_id, activity_id),
    CHECK (distance_m >= 0 AND moving_time_s >= 0 AND elapsed_time_s >= 0)
);

CREATE INDEX IF NOT EXISTS ix_activities_corredor_day ON activities (corredor_id, day);
"""

# TODO(Fase 5.5): antes da PRIMEIRA importação de histórico manual, criar em v3
# `CREATE UNIQUE INDEX ... ON activities(corredor_id, day) WHERE activity_id IS NULL`.
# O `ON CONFLICT` não dispara com `activity_id NULL` (o SQLite trata cada NULL
# como distinto), então hoje reimportar histórico manual duplicaria tudo.


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

            # Cada bloco é aditivo: uma versão nova nunca reescreve o que a
            # anterior gravou. Recriar `corredor_state` obrigaria os 50+
            # participantes a autorizar de novo, um por um, por mensagem.
            if versao < 1:
                conn.executescript(_SCHEMA_V1)
            if versao < 2:
                conn.executescript(_SCHEMA_V2)

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


def para_texto_local(momento: datetime) -> str:
    """Converte um horário LOCAL e ingênuo em texto, sem inventar fuso.

    Não use `para_texto` para isto: ele assume UTC no ingênuo e gravaria um
    instante que não é o da corrida. Aqui o fuso é descartado, nunca convertido
    — é a mesma regra do `start_date_local` no `ActivityService`.
    """
    if momento.tzinfo is not None:
        logger.debug("Horário local recebido com fuso (%s); o fuso é descartado.", momento.tzinfo)
        momento = momento.replace(tzinfo=None)
    return momento.isoformat()


def de_texto_local(valor: str) -> datetime:
    """Lê um horário gravado por `para_texto_local`, sempre devolvendo ingênuo.

    Diferente de `de_texto`, um valor ilegível aqui **levanta**. Virar `None`
    faria a corrida desaparecer da agregação diária e subnotificar a carga do
    dia sem nenhum sinal — enquanto no `corredor_state` uma data ilegível só
    custa uma página de API a mais.

    Raises:
        StateError: se o texto não for uma data ISO-8601.
    """
    try:
        momento = datetime.fromisoformat(valor)
    except (TypeError, ValueError) as erro:
        raise StateError(
            f"horário local inválido no banco: {valor!r} — a atividade não pode ser lida "
            "sem saber a que dia ela pertence"
        ) from erro
    return momento.replace(tzinfo=None)
