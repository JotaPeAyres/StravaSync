"""Testes do filtro e da conversão de atividades (Fase 3).

O teste mais importante deste arquivo é o do `start_date_local`: ler o campo
errado desloca a corrida de dia — e dia errado é **linha errada da planilha**.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.services.activity_service import ActivityService
from src.utils.errors import InvalidResponseError


def _corrida(**overrides) -> dict:
    padrao = {
        "id": 123456,
        "name": "Corrida da manhã",
        "type": "Run",
        "sport_type": "Run",
        "start_date": "2026-01-02T01:15:30Z",        # UTC: já é dia 2
        "start_date_local": "2026-01-01T22:15:30Z",  # local: ainda é dia 1
        "distance": 10250.5,
        "moving_time": 3300,
        "elapsed_time": 3450,
        "average_speed": 3.1,
        "average_heartrate": 152.4,
        "max_heartrate": 178.0,
        "total_elevation_gain": 88.0,
        "average_cadence": 84.5,
    }
    return {**padrao, **overrides}


@pytest.fixture
def servico() -> ActivityService:
    return ActivityService()


# -------------------------------------------------------------------- filtro


def test_mantem_corridas(servico):
    atividades = [_corrida(id=1), _corrida(id=2)]

    assert len(servico.only_runs(atividades)) == 2


@pytest.mark.parametrize("tipo", ["Ride", "Walk", "Swim", "WeightTraining", "VirtualRun"])
def test_descarta_o_que_nao_e_corrida(servico, tipo):
    assert servico.only_runs([_corrida(type=tipo)]) == []


def test_corrida_de_trilha_passa(servico):
    """`TrailRun` é `sport_type`; o `type` dela continua sendo "Run"."""
    atividades = [_corrida(type="Run", sport_type="TrailRun")]

    assert len(servico.only_runs(atividades)) == 1


def test_tipos_descartados_vao_para_o_log(servico, caplog):
    """É assim que se descobre quanta esteira a pesquisa está deixando de fora."""
    with caplog.at_level("DEBUG"):
        servico.only_runs([_corrida(type="VirtualRun"), _corrida(type="Ride")])

    assert "VirtualRun=1" in caplog.text
    assert "Ride=1" in caplog.text


def test_lista_vazia(servico):
    assert servico.only_runs([]) == []


# ---------------------------------------------------------------- conversão


def test_le_o_horario_local_e_nao_o_utc(servico):
    """Corrida às 22h de 01/01 pertence à linha de 01/01, não à de 02/01."""
    atividade = servico.to_activity(_corrida())

    assert atividade.date == datetime(2026, 1, 1, 22, 15, 30)  # noqa: DTZ001 — local ingênuo
    assert atividade.day.isoformat() == "2026-01-01"


def test_o_z_do_horario_local_e_descartado_nao_convertido(servico):
    """O `Z` do `start_date_local` é decorativo: o valor já está no fuso do atleta."""
    atividade = servico.to_activity(_corrida(start_date_local="2026-03-10T23:50:00Z"))

    assert atividade.date.hour == 23
    assert atividade.date.tzinfo is None
    assert atividade.day.isoformat() == "2026-03-10"


def test_le_o_start_date_utc_alem_do_local(servico):
    """As duas datas convivem porque respondem a perguntas diferentes.

    A local decide a linha da planilha; a UTC é a marca d'água do `after=`, que
    o Strava filtra por UTC. No payload de teste elas caem em dias diferentes.
    """
    atividade = servico.to_activity(_corrida())

    assert atividade.date == datetime(2026, 1, 1, 22, 15, 30)  # noqa: DTZ001 — local ingênuo
    assert atividade.start_date_utc == datetime(2026, 1, 2, 1, 15, 30, tzinfo=UTC)
    assert atividade.day.isoformat() == "2026-01-01"


@pytest.mark.parametrize("valor", [None, "ontem", ""])
def test_start_date_ausente_ou_invalido_nao_derruba_a_conversao(servico, valor):
    """Sem ele a busca seguinte repagina histórico — custa cota, não corrompe dado.

    Matar a coleta do participante por causa disso seria desproporcional; é a
    assimetria proposital em relação ao `start_date_local`, que é fatal.
    """
    payload = _corrida()
    if valor is None:
        del payload["start_date"]
    else:
        payload["start_date"] = valor

    atividade = servico.to_activity(payload)

    assert atividade.start_date_utc is None
    assert atividade.date == datetime(2026, 1, 1, 22, 15, 30)  # noqa: DTZ001 — segue válida


def test_mapeia_os_campos_do_strava(servico):
    atividade = servico.to_activity(_corrida())

    assert atividade.id == 123456
    assert atividade.name == "Corrida da manhã"
    assert atividade.distance_m == 10250.5
    assert atividade.distance_km == pytest.approx(10.2505)
    assert atividade.moving_time_s == 3300
    assert atividade.elapsed_time_s == 3450
    assert atividade.elevation_gain == 88.0
    assert atividade.cadence == 84.5
    assert atividade.average_heartrate == 152.4


def test_campos_opcionais_ausentes_viram_none(servico):
    """Corrida sem cinta de frequência cardíaca é normal, não erro."""
    payload = _corrida()
    for campo in ("average_heartrate", "max_heartrate", "total_elevation_gain", "average_cadence"):
        del payload[campo]

    atividade = servico.to_activity(payload)

    assert atividade.average_heartrate is None
    assert atividade.max_heartrate is None
    assert atividade.elevation_gain is None
    assert atividade.cadence is None


def test_calorias_sempre_none_na_listagem(servico):
    """`calories` só existe no endpoint por atividade — 1 requisição por corrida."""
    assert servico.to_activity(_corrida(calories=500)).calories is None


def test_elapsed_time_ausente_cai_no_tempo_em_movimento(servico):
    payload = _corrida()
    del payload["elapsed_time"]

    assert servico.to_activity(payload).elapsed_time_s == 3300


@pytest.mark.parametrize("campo", ["id", "distance", "moving_time", "start_date_local"])
def test_campo_obrigatorio_ausente_e_erro(servico, campo):
    payload = _corrida()
    del payload[campo]

    with pytest.raises(InvalidResponseError, match="Corrida da manhã"):
        servico.to_activity(payload)


def test_campo_obrigatorio_nao_numerico_e_erro(servico):
    with pytest.raises(InvalidResponseError, match="distance"):
        servico.to_activity(_corrida(distance="dez quilômetros"))


def test_horario_invalido_e_erro(servico):
    with pytest.raises(InvalidResponseError, match="start_date_local"):
        servico.to_activity(_corrida(start_date_local="ontem de manhã"))


def test_campo_opcional_estranho_nao_derruba_a_conversao(servico):
    assert servico.to_activity(_corrida(average_heartrate="alta")).average_heartrate is None


def test_converte_lista_preservando_a_ordem(servico):
    atividades = servico.to_activities([_corrida(id=1), _corrida(id=2), _corrida(id=3)])

    assert [a.id for a in atividades] == [1, 2, 3]


def test_agregacao_diaria_continua_na_fase_6(servico):
    with pytest.raises(NotImplementedError):
        servico.aggregate_daily([])
