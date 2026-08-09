"""Modelo de uma atividade (corrida) do Strava — persistida no SQLite.

Guarda os dados ricos por atividade; NÃO vai para o Excel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Activity:
    """Uma atividade individual do Strava."""

    id: int
    name: str
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
