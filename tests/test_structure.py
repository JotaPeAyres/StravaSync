"""Testes de fumaça da estrutura (Fase 1): módulos importáveis e modelo básico."""
from __future__ import annotations

from datetime import date


def test_main_importavel():
    import src.main

    assert callable(src.main.main)


def test_dailyload_pace():
    from src.models.daily_load import DailyLoad

    corrida = DailyLoad(day=date(2026, 1, 1), carga_km=10.0, tempo_total_s=3000)  # 50min/10km
    assert corrida.pace_min_km == 5.0

    descanso = DailyLoad(day=date(2026, 1, 2), carga_km=0.0, tempo_total_s=0)
    assert descanso.pace_min_km is None
