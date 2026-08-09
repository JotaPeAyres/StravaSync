"""Agregado de carga por dia — alimenta a planilha (abas Daily_Data e PACE)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DailyLoad:
    """Carga de um único dia, obtida somando as corridas do dia."""

    day: date
    carga_km: float            # Σ (distance_m / 1000)
    tempo_total_s: int         # Σ moving_time_s

    @property
    def pace_min_km(self) -> float | None:
        """Pace do dia em min/km (tempo_total / distância); None em dia sem corrida."""
        if self.carga_km <= 0:
            return None
        return (self.tempo_total_s / 60) / self.carga_km
