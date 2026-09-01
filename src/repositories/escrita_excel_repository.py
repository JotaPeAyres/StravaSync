"""Rastreia o que o próprio app escreveu por último em cada dia da planilha.

Existe por um motivo só: distinguir "chegou uma atividade nova para este dia,
o app está atualizando de novo" (rotina) de "um humano editou a célula depois
do corte" (exceção, ver `CLAUDE.md` — Fase 5.5). O valor **atual** da célula
não basta para essa distinção — só comparando contra o que o app gravou da
última vez.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime

from src.services.database_service import para_texto
from src.utils.errors import StateError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class EscritaExcelRepository:
    """Lê e grava a tabela `excel_escritas`."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        agora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._conn = conn
        self._agora = agora

    def carregar(self, corredor_id: str) -> dict[date, tuple[float, int]]:
        """Toda a baseline conhecida de um corredor — insumo de `ExcelService.sincronizar`."""
        linhas = self._executar(
            "SELECT day, carga_km, tempo_total_s FROM excel_escritas WHERE corredor_id = ?",
            (corredor_id,),
        ).fetchall()
        return {
            date.fromisoformat(linha["day"]): (linha["carga_km"], linha["tempo_total_s"])
            for linha in linhas
        }

    def registrar_muitas(
        self, corredor_id: str, escritas: Mapping[date, tuple[float, int]]
    ) -> None:
        """Grava a baseline nova — o `novas_escritas` que `sincronizar` acabou de devolver.

        Idempotente por dia (`INSERT ... ON CONFLICT DO UPDATE`): repetir a
        mesma sincronização não duplica, só atualiza `atualizado_em`.
        """
        if not escritas:
            return

        carimbo = para_texto(self._agora())
        parametros = [
            {
                "corredor_id": corredor_id,
                "day": dia.isoformat(),
                "carga_km": carga_km,
                "tempo_total_s": tempo_total_s,
                "atualizado_em": carimbo,
            }
            for dia, (carga_km, tempo_total_s) in escritas.items()
        ]

        try:
            self._conn.executemany(
                """
                INSERT INTO excel_escritas (corredor_id, day, carga_km, tempo_total_s, atualizado_em)
                VALUES (:corredor_id, :day, :carga_km, :tempo_total_s, :atualizado_em)
                ON CONFLICT (corredor_id, day) DO UPDATE SET
                    carga_km      = excluded.carga_km,
                    tempo_total_s = excluded.tempo_total_s,
                    atualizado_em = excluded.atualizado_em
                """,
                parametros,
            )
            self._conn.commit()
        except sqlite3.Error as erro:
            self._conn.rollback()
            raise StateError(f"falha ao gravar a baseline do Excel: {erro}") from erro

    def _executar(self, sql: str, parametros: tuple = ()) -> sqlite3.Cursor:
        try:
            return self._conn.execute(sql, parametros)
        except sqlite3.Error as erro:
            raise StateError(f"falha ao acessar a baseline do Excel: {erro}") from erro
