"""Modelo de uma atividade (corrida) do Strava — persistida no SQLite.

Guarda os dados ricos por atividade; NÃO vai para o Excel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Activity:
    """Uma atividade individual do Strava."""

    id: int
    name: str
    # Horário LOCAL do corredor, sem timezone (o `start_date_local` do Strava).
    # A planilha é indexada pelo dia local: uma corrida às 22h de 01/01 pertence
    # à linha de 01/01, e não à de 02/01 (que é o dia em UTC).
    date: datetime
    distance_m: float          # metros (unidade do Strava)
    moving_time_s: int         # segundos
    elapsed_time_s: int
    type: str = "Run"
    average_speed: float | None = None
    average_heartrate: float | None = None
    max_heartrate: float | None = None
    elevation_gain: float | None = None
    calories: float | None = None
    cadence: float | None = None

    @property
    def day(self) -> date:
        """Dia da corrida — chave de agregação para o `DailyLoad`."""
        return self.date.date()

    @property
    def distance_km(self) -> float:
        """Distância em km (o Strava entrega metros; o Excel espera km)."""
        return self.distance_m / 1000
