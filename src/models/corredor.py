"""Participante da pesquisa — um corredor e a planilha que lhe pertence.

Cada corredor entra no estudo numa data própria (`start_date`) e tem a sua
própria planilha. O `refresh_token` aqui é o **inicial**, usado para o primeiro
acesso; o token corrente é estado mutável e vive no SQLite, porque o Strava pode
devolver um token novo a cada renovação.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Corredor:
    """Um participante da pesquisa."""

    id: str
    nome: str
    # Segredo: fora do repr para não vazar em log ou traceback.
    refresh_token: str = field(repr=False)
    start_date: date
    cutover_date: date
    excel_path: Path

    @property
    def adota_planilha_existente(self) -> bool:
        """True quando há histórico manual anterior ao corte, a ser preservado."""
        return self.cutover_date > self.start_date

    def dia_da_planilha(self, dia: date) -> int:
        """Converte uma data no número do dia (`Dia 1` = `start_date`).

        Valores <= 0 são anteriores à entrada do corredor na pesquisa e não têm
        linha na planilha — quem chama decide descartar.
        """
        return (dia - self.start_date).days + 1

    def __str__(self) -> str:
        return f"{self.id} ({self.nome})"
