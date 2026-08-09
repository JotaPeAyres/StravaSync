"""Regras de negócio das atividades: filtrar corridas, validar e agregar por dia.

Implementação nas Fases 3/6.
"""
from __future__ import annotations


class ActivityService:
    """Converte e agrega atividades da API no modelo interno."""

    def only_runs(self, activities: list[dict]) -> list[dict]:
        """Filtra apenas atividades do tipo Run. (Stub — Fase 3.)"""
        raise NotImplementedError  # TODO(Fase 3)

    def aggregate_daily(self, activities: list) -> list:
        """Agrega as corridas por dia (DailyLoad). (Stub — Fase 6.)"""
        raise NotImplementedError  # TODO(Fase 6)
