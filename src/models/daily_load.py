"""Agregado de carga por dia — alimenta a planilha (abas Daily_Data e PACE)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class DailyLoad:
    """Carga de um único dia, obtida somando as corridas do dia."""

    day: date
    carga_km: float            # Σ (distance_m / 1000)
    tempo_total_s: int         # Σ moving_time_s

    @property
    def is_rest_day(self) -> bool:
        """Dia sem corrida: vai para o Excel com Carga 0 e Pace/Tempo em branco."""
        return self.carga_km <= 0

    @property
    def pace_min_km(self) -> float | None:
        """Pace do dia em min/km (tempo_total / distância); None em dia sem corrida."""
        if self.is_rest_day:
            return None
        return (self.tempo_total_s / 60) / self.carga_km

    @property
    def tempo_total(self) -> timedelta | None:
        """Tempo total como `timedelta` — o openpyxl grava isso como duração h:mm:ss."""
        if self.is_rest_day:
            return None
        return timedelta(seconds=self.tempo_total_s)

    @classmethod
    def dia_de_descanso(cls, day: date) -> DailyLoad:
        """Cria o registro de um dia sem corrida (a grade do Excel é contígua)."""
        return cls(day=day, carga_km=0.0, tempo_total_s=0)
