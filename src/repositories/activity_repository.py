"""Persistência de atividades no SQLite e dedupe.

Nunca conhece a API do Strava: recebe modelos e devolve modelos.

**O dedupe é o índice `UNIQUE (corredor_id, activity_id)`, não uma checagem
prévia.** A busca da Fase 3 refaz os últimos dois dias a cada execução, então
reencontrar a mesma corrida é rotina — e um "pula se já existe" descartaria a
correção de um atleta que ajustou a distância depois do upload, deixando a carga
do dia errada para sempre. Daí o upsert.

A chave é **composta** de propósito. Um `UNIQUE` global em `activity_id` faria a
sincronização de um participante capturar a linha de outro, caso duas inscrições
apontem para a mesma conta do Strava. Com a chave composta, o pior caso vira uma
linha duplicada — visível e corrigível —, e não um dado atribuído à pessoa errada.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from src.models.activity import Activity
from src.models.registro_historico import RegistroHistorico
from src.services.database_service import (
    de_texto,
    de_texto_local,
    para_texto,
    para_texto_local,
)
from src.utils.errors import StateError
from src.utils.logger import get_logger

logger = get_logger(__name__)

ORIGEM_STRAVA = "strava"
ORIGEM_MANUAL = "manual"

# Sem CHECK no banco: o roadmap admite outras fontes (Garmin, Polar), e alterar
# um CHECK no SQLite exige reconstruir a tabela.

_COLUNAS = """
    id, corredor_id, activity_id, origem, name, type, day, date_local,
    start_date_utc, distance_m, moving_time_s, elapsed_time_s, average_speed,
    average_heartrate, max_heartrate, elevation_gain, calories, cadence,
    criado_em, atualizado_em
"""


@dataclass(frozen=True)
class ResultadoGravacao:
    """O que uma gravação mudou — insumo da Fase 6 para saber o que reescrever."""

    novas: int
    atualizadas: int
    # Inclui o dia ANTIGO quando o atleta muda a data da corrida no Strava. Sem
    # isso, a linha antiga da planilha ficaria com carga fantasma e nada
    # apontaria para lá.
    dias_afetados: frozenset[date] = frozenset()

    @property
    def total(self) -> int:
        return self.novas + self.atualizadas


class ActivityRepository:
    """Salva e consulta atividades no banco local."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        agora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._conn = conn
        self._agora = agora

    def existe(self, corredor_id: str, activity_id: int) -> bool:
        """True se este corredor já tem esta atividade.

        **Não é usado no caminho de sincronização** — lá o dedupe é o índice
        UNIQUE, que não gasta uma consulta extra por corrida nem sofre do
        intervalo entre checar e gravar. Isto aqui é afordância de diagnóstico e
        de teste.
        """
        linha = self._executar(
            "SELECT 1 FROM activities WHERE corredor_id = ? AND activity_id = ?",
            (corredor_id, activity_id),
        ).fetchone()
        return linha is not None

    def salvar(
        self, corredor_id: str, activity: Activity, *, origem: str = ORIGEM_STRAVA
    ) -> ResultadoGravacao:
        """Grava uma atividade. Atalho para `salvar_muitas`."""
        return self.salvar_muitas(corredor_id, [activity], origem=origem)

    def salvar_muitas(
        self,
        corredor_id: str,
        activities: Iterable[Activity],
        *,
        origem: str = ORIGEM_STRAVA,
    ) -> ResultadoGravacao:
        """Grava as atividades de um corredor, tudo ou nada.

        A atomicidade não é preciosismo: logo depois desta gravação a marca
        d'água do `after=` avança. Lote pela metade com a marca d'água avançada
        significa corridas perdidas **para sempre**, e a folga da busca é de só
        dois dias.

        O `commit()` acontece aqui, uma vez por corredor — é o que torna a
        execução retomável: se a coleta morrer no corredor 60, os 59 anteriores
        já estão gravados.
        """
        lote = tuple(activities)
        if not lote:
            # Um corredor que não correu não deve gerar escrita nenhuma.
            return ResultadoGravacao(0, 0)

        carimbo = para_texto(self._agora())
        dias_anteriores = self._dias_gravados(corredor_id, lote)

        parametros = [self._parametros(corredor_id, a, origem, carimbo) for a in lote]
        self._executar_muitas(_SQL_UPSERT, parametros)
        self._conn.commit()

        return self._resumir(corredor_id, lote, dias_anteriores)

    def por_dia(self, corredor_id: str, dia: date) -> tuple[Activity, ...]:
        """Todas as atividades de um dia.

        A Fase 6 agrega a partir daqui, e não do que a API acabou de devolver:
        se um dia tem duas corridas e a segunda chega numa execução posterior,
        agregar só o retorno da API escreveria a carga apenas da segunda.
        """
        return self.por_periodo(corredor_id, dia, dia)

    def por_periodo(self, corredor_id: str, inicio: date, fim: date) -> tuple[Activity, ...]:
        """Atividades entre `inicio` e `fim`, **incluindo os dois extremos**.

        Inclusivo porque a grade da planilha é: o `Dia 1` e o último dia contam.
        Intervalo invertido devolve vazio, sem levantar.

        ⚠️ Não filtra por `origem`. Se a Fase 5.5 importar histórico manual e o
        `cutover_date` estiver mal ajustado, o mesmo dia pode ter a linha manual
        e a do Strava — e somá-las dobraria a carga. É para isso que o
        `cutover_date` existe.
        """
        linhas = self._executar(
            f"""
            SELECT {_COLUNAS} FROM activities
             WHERE corredor_id = ? AND day BETWEEN ? AND ?
             ORDER BY day, date_local, id
            """,
            (corredor_id, inicio.isoformat(), fim.isoformat()),
        ).fetchall()
        return tuple(_para_modelo(linha) for linha in linhas)

    def ultimo_evento_em(self, corredor_id: str) -> datetime | None:
        """O `start_date` (UTC) mais recente já gravado para este corredor.

        É a marca d'água do `after=`. Sai do **banco**, e não do retorno da API,
        justamente para nunca ultrapassar o que foi de fato persistido — uma
        marca d'água à frente dos dados pularia corridas que falharam ao gravar.

        Linhas manuais (`start_date_utc IS NULL`) são ignoradas pelo `MAX`: o que
        foi digitado à mão não pode empurrar a busca do Strava.
        """
        linha = self._executar(
            "SELECT MAX(start_date_utc) AS ultimo FROM activities WHERE corredor_id = ?",
            (corredor_id,),
        ).fetchone()
        # O MAX é lexicográfico; só equivale ao cronológico porque `para_texto`
        # normaliza tudo para UTC com offset explícito. Valor editado à mão no
        # banco quebra essa ordenação sem erro nenhum.
        return de_texto(linha["ultimo"]) if linha is not None else None

    def por_start_date_utc(self, corredor_id: str, desde: datetime) -> tuple[Activity, ...]:
        """Atividades de origem Strava com `start_date_utc >= desde`.

        Base da detecção de atividade apagada (Fase 6): para o mesmo `after=`
        usado numa busca, tudo aqui deveria ter voltado na resposta do Strava
        — o que não voltou foi excluído por lá, sem custar requisição nenhuma
        para descobrir.

        Linhas manuais (`activity_id IS NULL`) nunca aparecem: elas não têm
        contrapartida no Strava para "sumir".
        """
        linhas = self._executar(
            f"""
            SELECT {_COLUNAS} FROM activities
             WHERE corredor_id = ? AND activity_id IS NOT NULL AND start_date_utc >= ?
             ORDER BY start_date_utc
            """,
            (corredor_id, para_texto(desde)),
        ).fetchall()
        return tuple(_para_modelo(linha) for linha in linhas)

    def importar_historico(
        self, corredor_id: str, registros: Iterable[RegistroHistorico]
    ) -> ResultadoGravacao:
        """Importa o período manual (Fase 5.5) como atividades de origem `manual`.

        Só dias com corrida de fato viram linha — um dia de descanso nunca é
        uma atividade, nem quando a sincronização vem do Strava.

        O upsert usa o índice **parcial** `(corredor_id, day) WHERE activity_id
        IS NULL` (schema v3), e não o `UNIQUE (corredor_id, activity_id)` da
        Fase 4: histórico manual não tem `activity_id`, e sem esse índice o
        `ON CONFLICT` não dispara — rodar a adoção duas vezes duplicaria o
        período inteiro a cada vez, em vez de atualizar.

        `start_date_utc` fica `NULL` de propósito: não sabemos a hora exata de
        uma corrida digitada à mão, e `ultimo_evento_em` já ignora linhas
        assim — histórico manual nunca deve empurrar a marca d'água do
        `after=`.
        """
        corridas = [registro for registro in registros if registro.tem_corrida]
        if not corridas:
            return ResultadoGravacao(0, 0)

        for registro in corridas:
            if registro.tempo_total_s is None:
                logger.warning(
                    "Corredor %s: %s importado sem tempo conhecido (PACE não "
                    "cobria o dia) — pace daquele dia fica indeterminado.",
                    corredor_id,
                    registro.day.isoformat(),
                )

        carimbo = para_texto(self._agora())
        dias_existentes = self._dias_manuais_existentes(corredor_id, corridas)

        parametros = [self._parametros_manual(corredor_id, r, carimbo) for r in corridas]
        self._executar_muitas(_SQL_UPSERT_MANUAL, parametros)
        self._conn.commit()

        novas = sum(1 for r in corridas if r.day.isoformat() not in dias_existentes)
        atualizadas = len(corridas) - novas
        return ResultadoGravacao(novas, atualizadas, frozenset(r.day for r in corridas))

    # --------------------------------------------------------------- interno

    def _dias_manuais_existentes(
        self, corredor_id: str, registros: list[RegistroHistorico]
    ) -> set[str]:
        """Dias do lote que já têm linha manual gravada — só para separar novas/atualizadas."""
        dias = [r.day.isoformat() for r in registros]
        marcadores = ",".join("?" * len(dias))
        linhas = self._executar(
            f"SELECT day FROM activities WHERE corredor_id = ? AND activity_id IS NULL "
            f"AND day IN ({marcadores})",
            (corredor_id, *dias),
        ).fetchall()
        return {linha["day"] for linha in linhas}

    def _parametros_manual(
        self, corredor_id: str, registro: RegistroHistorico, carimbo: str
    ) -> dict:
        """Uma linha manual a partir do `RegistroHistorico` lido da planilha."""
        tempo_s = registro.tempo_total_s if registro.tempo_total_s is not None else 0
        return {
            "corredor_id": corredor_id,
            "day": registro.day.isoformat(),
            "date_local": para_texto_local(datetime.combine(registro.day, time.min)),
            "distance_m": registro.carga_km * 1000,
            "moving_time_s": tempo_s,
            "elapsed_time_s": tempo_s,
            "carimbo": carimbo,
        }

    def _dias_gravados(self, corredor_id: str, lote: tuple[Activity, ...]) -> dict[int, str]:
        """Dia atualmente gravado para cada atividade do lote que já existe.

        Uma consulta só resolve três perguntas: quais são novas, quais são
        atualizações e de que dia saíram as que mudaram de data.
        """
        ids = [a.id for a in lote if a.id is not None]
        if not ids:
            return {}

        marcadores = ",".join("?" * len(ids))
        linhas = self._executar(
            # A interpolação é só da lista de marcadores `?`; os valores vão
            # como parâmetros, nunca no texto do SQL.
            f"SELECT activity_id, day FROM activities "
            f"WHERE corredor_id = ? AND activity_id IN ({marcadores})",
            (corredor_id, *ids),
        ).fetchall()
        return {linha["activity_id"]: linha["day"] for linha in linhas}

    def _resumir(
        self,
        corredor_id: str,
        lote: tuple[Activity, ...],
        dias_anteriores: dict[int, str],
    ) -> ResultadoGravacao:
        """Conta novas/atualizadas e reúne os dias que precisam ser reescritos."""
        novas = atualizadas = 0
        dias: set[date] = set()

        for atividade in lote:
            dias.add(atividade.day)
            anterior = dias_anteriores.get(atividade.id)

            if anterior is None:
                novas += 1
                continue

            atualizadas += 1
            if anterior != atividade.day.isoformat():
                # Dois dias ficam errados: o novo ganha carga e o antigo fica
                # com carga fantasma. É raro e confuso o bastante para merecer
                # uma pista a quem for investigar a planilha depois.
                dias.add(date.fromisoformat(anterior))
                logger.warning(
                    "Corredor %s: a atividade %d mudou de %s para %s no Strava; "
                    "os dois dias precisam ser reescritos.",
                    corredor_id,
                    atividade.id,
                    anterior,
                    atividade.day.isoformat(),
                )

        return ResultadoGravacao(novas, atualizadas, frozenset(dias))

    def _parametros(
        self, corredor_id: str, atividade: Activity, origem: str, carimbo: str | None
    ) -> dict:
        """Uma linha da tabela a partir do modelo.

        Ponto único de escrita de `day` e `date_local`, que são redundantes de
        propósito — a redundância só é segura porque nasce daqui.
        """
        return {
            "corredor_id": corredor_id,
            "activity_id": atividade.id,
            "origem": origem,
            "name": atividade.name,
            "type": atividade.type,
            "day": atividade.day.isoformat(),
            "date_local": para_texto_local(atividade.date),
            "start_date_utc": para_texto(atividade.start_date_utc),
            "distance_m": atividade.distance_m,
            "moving_time_s": atividade.moving_time_s,
            "elapsed_time_s": atividade.elapsed_time_s,
            "average_speed": atividade.average_speed,
            "average_heartrate": atividade.average_heartrate,
            "max_heartrate": atividade.max_heartrate,
            "elevation_gain": atividade.elevation_gain,
            "calories": atividade.calories,
            "cadence": atividade.cadence,
            "carimbo": carimbo,
        }

    def _executar(self, sql: str, parametros: tuple | dict = ()) -> sqlite3.Cursor:
        """Executa e traduz falhas do SQLite para `StateError`."""
        try:
            return self._conn.execute(sql, parametros)
        except sqlite3.Error as erro:
            self._desfazer()
            raise StateError(f"falha ao acessar as atividades: {erro}") from erro

    def _executar_muitas(self, sql: str, parametros: list[dict]) -> None:
        """Grava o lote inteiro, ou nenhuma linha dele."""
        try:
            self._conn.executemany(sql, parametros)
        except sqlite3.Error as erro:
            self._desfazer()
            raise StateError(f"falha ao gravar as atividades: {erro}") from erro

    def _desfazer(self) -> None:
        """Fecha a transação implícita antes de propagar o erro.

        Sem isto, um `executemany` que falha no meio deixa linhas parciais numa
        transação aberta — e o `commit()` do **corredor seguinte** as gravaria
        junto, misturando os dados de dois participantes.
        """
        try:
            self._conn.rollback()
        except sqlite3.Error:
            logger.debug("Rollback falhou; a conexão provavelmente já está inutilizável.")


# `INSERT OR REPLACE` seria mais curto e está errado: ele é DELETE + INSERT, o
# que zeraria `criado_em`, trocaria o id e apagaria toda coluna não citada.
#
# Fora do SET, de propósito: `corredor_id` e `activity_id` (são a identidade —
# reescrevê-los trocaria o dono da corrida), `origem` (uma sincronização não
# reetiqueta em silêncio uma linha curada por um humano) e `criado_em` (é quando
# a pesquisa viu a corrida pela primeira vez).
#
# A regra do COALESCE: use onde `None` significa "não perguntamos"; sobrescreva
# onde `None` significa "o atleta não tem esse dado". `calories` nunca vem da
# listagem, então sobrescrever apagaria o que outro caminho preencheu. Já uma FC
# ausente é "correu sem cinta" — informação legítima, e blindá-la com COALESCE
# tornaria impossível corrigir um valor errado.
_SQL_UPSERT = """
INSERT INTO activities (
    corredor_id, activity_id, origem, name, type, day, date_local, start_date_utc,
    distance_m, moving_time_s, elapsed_time_s, average_speed, average_heartrate,
    max_heartrate, elevation_gain, calories, cadence, criado_em, atualizado_em
) VALUES (
    :corredor_id, :activity_id, :origem, :name, :type, :day, :date_local, :start_date_utc,
    :distance_m, :moving_time_s, :elapsed_time_s, :average_speed, :average_heartrate,
    :max_heartrate, :elevation_gain, :calories, :cadence, :carimbo, :carimbo
)
ON CONFLICT (corredor_id, activity_id) DO UPDATE SET
    name              = excluded.name,
    type              = excluded.type,
    day               = excluded.day,
    date_local        = excluded.date_local,
    start_date_utc    = COALESCE(excluded.start_date_utc, activities.start_date_utc),
    distance_m        = excluded.distance_m,
    moving_time_s     = excluded.moving_time_s,
    elapsed_time_s    = excluded.elapsed_time_s,
    average_speed     = excluded.average_speed,
    average_heartrate = excluded.average_heartrate,
    max_heartrate     = excluded.max_heartrate,
    elevation_gain    = excluded.elevation_gain,
    calories          = COALESCE(excluded.calories, activities.calories),
    cadence           = excluded.cadence,
    atualizado_em     = excluded.atualizado_em
"""

# Conflito no índice PARCIAL da v3 — `(corredor_id, day) WHERE activity_id IS
# NULL` —, não no `UNIQUE (corredor_id, activity_id)` da v2: linha manual não
# tem `activity_id`, e o SQLite trata cada `NULL` como distinto, então o
# upsert de cima nunca dispararia para elas.
_SQL_UPSERT_MANUAL = """
INSERT INTO activities (
    corredor_id, activity_id, origem, name, type, day, date_local, start_date_utc,
    distance_m, moving_time_s, elapsed_time_s, criado_em, atualizado_em
) VALUES (
    :corredor_id, NULL, 'manual', '', 'Run', :day, :date_local, NULL,
    :distance_m, :moving_time_s, :elapsed_time_s, :carimbo, :carimbo
)
ON CONFLICT (corredor_id, day) WHERE activity_id IS NULL DO UPDATE SET
    date_local     = excluded.date_local,
    distance_m     = excluded.distance_m,
    moving_time_s  = excluded.moving_time_s,
    elapsed_time_s = excluded.elapsed_time_s,
    atualizado_em  = excluded.atualizado_em
"""


def _para_modelo(linha: sqlite3.Row) -> Activity:
    """Converte uma linha da tabela no modelo `Activity`."""
    return Activity(
        id=linha["activity_id"],
        name=linha["name"] or "",
        date=de_texto_local(linha["date_local"]),
        distance_m=linha["distance_m"],
        moving_time_s=linha["moving_time_s"],
        elapsed_time_s=linha["elapsed_time_s"],
        type=linha["type"],
        average_speed=linha["average_speed"],
        average_heartrate=linha["average_heartrate"],
        max_heartrate=linha["max_heartrate"],
        elevation_gain=linha["elevation_gain"],
        calories=linha["calories"],
        cadence=linha["cadence"],
        start_date_utc=de_texto(linha["start_date_utc"]),
    )
