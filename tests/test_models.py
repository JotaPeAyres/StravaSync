"""Testes dos modelos e das conversões de unidade Strava → Excel (Fase 2).

As seções `Corredor`/`EstadoCorredor`/`RegistroHistorico`/`StravaToken` foram
acrescentadas na Fase 8 — antes só eram exercitadas indiretamente, via
repositórios e serviços.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from src.models.activity import Activity
from src.models.corredor import Corredor
from src.models.daily_load import DailyLoad
from src.models.estado_corredor import EstadoCorredor
from src.models.registro_historico import RegistroHistorico
from src.models.strava_token import StravaToken
from src.utils.errors import InvalidResponseError


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


# ------------------------------------------------------------------- Corredor


def _corredor(**overrides) -> Corredor:
    padrao = {
        "id": "p001",
        "nome": "Ana",
        "refresh_token": "token-secreto",
        "start_date": date(2026, 1, 1),
        "cutover_date": date(2026, 1, 1),
        "excel_path": Path("p001.xlsx"),
    }
    return Corredor(**{**padrao, **overrides})


def test_corredor_adota_planilha_existente_true_quando_cutover_depois_do_start():
    assert _corredor(cutover_date=date(2026, 1, 5)).adota_planilha_existente is True


def test_corredor_adota_planilha_existente_false_quando_igual_ao_start():
    assert _corredor().adota_planilha_existente is False


def test_corredor_dia_da_planilha_dia_1_e_o_start_date():
    corredor = _corredor(start_date=date(2026, 1, 1))

    assert corredor.dia_da_planilha(date(2026, 1, 1)) == 1
    assert corredor.dia_da_planilha(date(2026, 1, 2)) == 2


def test_corredor_dia_da_planilha_negativo_para_data_anterior_ao_start():
    corredor = _corredor(start_date=date(2026, 1, 10))

    assert corredor.dia_da_planilha(date(2026, 1, 5)) == -4


def test_corredor_str_usa_id_e_nome():
    assert str(_corredor(id="p001", nome="Ana")) == "p001 (Ana)"


def test_corredor_refresh_token_fora_do_repr():
    assert "segredo-supersecreto" not in repr(_corredor(refresh_token="segredo-supersecreto"))


# -------------------------------------------------------------- EstadoCorredor


def test_estado_corredor_inscrito_true_com_refresh_token_e_sem_reinscricao():
    assert EstadoCorredor(corredor_id="p001", refresh_token="tok").inscrito is True


def test_estado_corredor_inscrito_false_sem_refresh_token():
    assert EstadoCorredor(corredor_id="p001", refresh_token="").inscrito is False


def test_estado_corredor_inscrito_false_quando_precisa_reinscricao():
    estado = EstadoCorredor(corredor_id="p001", refresh_token="tok", precisa_reinscricao=True)
    assert estado.inscrito is False


def test_estado_corredor_token_de_acesso_utilizavel_none_sem_access_token():
    assert EstadoCorredor(corredor_id="p001").token_de_acesso_utilizavel() is None


def test_estado_corredor_token_de_acesso_utilizavel_none_quando_expira_dentro_da_margem():
    estado = EstadoCorredor(
        corredor_id="p001",
        access_token="tok",
        access_token_expira_em=datetime.now(UTC) + timedelta(seconds=100),
    )
    assert estado.token_de_acesso_utilizavel(margem_s=300) is None


def test_estado_corredor_token_de_acesso_utilizavel_devolve_o_token_quando_sobra_margem():
    estado = EstadoCorredor(
        corredor_id="p001",
        access_token="tok-valido",
        access_token_expira_em=datetime.now(UTC) + timedelta(hours=6),
    )
    assert estado.token_de_acesso_utilizavel() == "tok-valido"


def test_estado_corredor_segredos_fora_do_repr():
    estado = EstadoCorredor(
        corredor_id="p001", refresh_token="refresh-secreto", access_token="access-secreto"
    )
    texto = repr(estado)

    assert "refresh-secreto" not in texto
    assert "access-secreto" not in texto


# ----------------------------------------------------------- RegistroHistorico


def test_registro_historico_tem_corrida_true_quando_carga_positiva():
    assert RegistroHistorico(day=date(2026, 1, 1), carga_km=5.0).tem_corrida is True


def test_registro_historico_tem_corrida_false_quando_carga_zero():
    assert RegistroHistorico(day=date(2026, 1, 1), carga_km=0.0).tem_corrida is False


def test_registro_historico_tempo_total_pode_ser_none():
    assert RegistroHistorico(day=date(2026, 1, 1), carga_km=5.0).tempo_total_s is None


# ----------------------------------------------------------------- StravaToken


def _payload(**overrides) -> dict:
    padrao = {
        "access_token": "access-123",
        "refresh_token": "refresh-123",
        "expires_at": int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()),
        "athlete": {"id": 777},
    }
    return {**padrao, **overrides}


def test_strava_token_expirado_true_apos_expira_em():
    token = StravaToken(access_token="a", refresh_token="r", expira_em=datetime.now(UTC) - timedelta(seconds=1))
    assert token.expirado is True


def test_strava_token_valido_por_true_quando_sobra_margem():
    token = StravaToken(access_token="a", refresh_token="r", expira_em=datetime.now(UTC) + timedelta(hours=1))
    assert token.valido_por(300) is True


def test_strava_token_valido_por_false_quando_expira_dentro_da_margem():
    token = StravaToken(
        access_token="a", refresh_token="r", expira_em=datetime.now(UTC) + timedelta(seconds=100)
    )
    assert token.valido_por(300) is False


def test_strava_token_from_payload_constroi_com_campos_completos():
    token = StravaToken.from_payload(_payload())

    assert token.access_token == "access-123"
    assert token.refresh_token == "refresh-123"
    assert token.athlete_id == 777


def test_strava_token_from_payload_athlete_id_none_quando_ausente():
    assert StravaToken.from_payload(_payload(athlete={})).athlete_id is None


def test_strava_token_from_payload_athlete_id_none_quando_malformado():
    assert StravaToken.from_payload(_payload(athlete={"id": "nao-numerico"})).athlete_id is None


def test_strava_token_from_payload_scope_do_payload_tem_precedencia_sobre_o_parametro():
    token = StravaToken.from_payload(_payload(scope="activity:read_all"), scope="outro")
    assert token.scope == "activity:read_all"


def test_strava_token_from_payload_usa_scope_do_parametro_quando_payload_nao_traz():
    token = StravaToken.from_payload(_payload(), scope="activity:read_all")
    assert token.scope == "activity:read_all"


@pytest.mark.parametrize(
    "overrides",
    [
        {"access_token": ""},
        {"refresh_token": ""},
        {"expires_at": None},
        {"expires_at": "nao-e-um-numero"},
    ],
)
def test_strava_token_from_payload_levanta_com_campo_obrigatorio_invalido(overrides):
    with pytest.raises(InvalidResponseError):
        StravaToken.from_payload(_payload(**overrides))


def test_strava_token_segredos_fora_do_repr():
    token = StravaToken(
        access_token="access-secreto", refresh_token="refresh-secreto", expira_em=datetime.now(UTC)
    )
    texto = repr(token)

    assert "access-secreto" not in texto
    assert "refresh-secreto" not in texto
