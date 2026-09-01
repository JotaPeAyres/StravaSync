"""Modelo de uma atividade (corrida) do Strava — persistida no SQLite.

Guarda os dados ricos por atividade; NÃO vai para o Excel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Activity:
    """Uma atividade individual do Strava — ou, desde a Fase 5.5, um dia do
    histórico manual importado da planilha (`id=None`, sem contrapartida no
    Strava).
    """

    name: str
    # Horário LOCAL do corredor, sem timezone (o `start_date_local` do Strava).
    # A planilha é indexada pelo dia local: uma corrida às 22h de 01/01 pertence
    # à linha de 01/01, e não à de 02/01 (que é o dia em UTC).
    date: datetime
    distance_m: float          # metros (unidade do Strava)
    moving_time_s: int         # segundos
    elapsed_time_s: int
    # `None` só para histórico manual (Fase 5.5): aquele período não tem
    # `activity_id` do Strava — é o que distingue as duas origens no banco
    # (`activities.origem`). Vem depois dos campos obrigatórios porque
    # dataclass exige que todo campo com padrão venha por último.
    id: int | None = None
    type: str = "Run"
    average_speed: float | None = None
    average_heartrate: float | None = None
    max_heartrate: float | None = None
    elevation_gain: float | None = None
    calories: float | None = None
    cadence: float | None = None
    # Instante ABSOLUTO da largada (o `start_date` do Strava, em UTC). Não é o
    # mesmo que `date`: uma corrida às 22h de 01/01 local pode ser 01:15 de
    # 02/01 em UTC. Serve à marca d'água do `after=`, que o Strava filtra por
    # UTC — enquanto `date` decide a linha da planilha. Opcional porque a
    # ausência dele encarece a busca seguinte, mas não invalida a corrida.
    start_date_utc: datetime | None = None

    @property
    def day(self) -> date:
        """Dia da corrida — chave de agregação para o `DailyLoad`."""
        return self.date.date()

    @property
    def distance_km(self) -> float:
        """Distância em km (o Strava entrega metros; o Excel espera km)."""
        return self.distance_m / 1000
