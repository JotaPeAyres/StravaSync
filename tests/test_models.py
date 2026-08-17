"""Testes dos modelos e das conversões de unidade Strava → Excel (Fase 2)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from src.models.activity import Activity
from src.models.daily_load import DailyLoad


def _corrida(hora: int = 6, minuto: int = 30, **overrides) -> Activity:
    """Corrida de exemplo em 01/01/2026, no horário local informado."""
    padrao = {
        "id": 1,
        "name": "Corrida matinal",
        # Ingênua de propósito: é horário local do corredor (ver Activity.date).
        "date": datetime(2026, 1, 1, hora, minuto),  # noqa: DTZ001
        "distance_m": 10_000.0,
        "moving_time_s": 3000,
        "elapsed_time_s": 3100,
    }
    return Activity(**{**padrao, **overrides})


def test_activity_converte_metros_para_km():
    assert _corrida(distance_m=10_500.0).distance_km == 10.5


def test_activity_day_ignora_a_hora():
    """`day` é a chave de agregação: duas corridas no mesmo dia caem no mesmo DailyLoad."""
    manha = _corrida(hora=6, minuto=30)
    noite = _corrida(hora=19, minuto=45)

    assert manha.day == noite.day == date(2026, 1, 1)


def test_activity_day_usa_o_dia_local():
    """Corrida tarde da noite fica no dia local — o dia em UTC seria o seguinte."""
    assert _corrida(hora=22, minuto=10).day == date(2026, 1, 1)


def test_dailyload_pace():
    corrida = DailyLoad(day=date(2026, 1, 1), carga_km=10.0, tempo_total_s=3000)  # 50min/10km

    assert corrida.pace_min_km == 5.0
    assert corrida.tempo_total == timedelta(minutes=50)
    assert corrida.is_rest_day is False


def test_dailyload_dia_de_descanso():
    """Dia sem corrida vai ao Excel com Carga 0 e Pace/Tempo em branco."""
    descanso = DailyLoad.dia_de_descanso(date(2026, 1, 2))

    assert descanso.is_rest_day is True
    assert descanso.carga_km == 0.0
    assert descanso.pace_min_km is None
    assert descanso.tempo_total is None
