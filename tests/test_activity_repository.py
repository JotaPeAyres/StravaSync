"""Testes da persistência de atividades (Fase 4).

O que este módulo erra vira carga errada na planilha de alguém — e a planilha é
o produto da pesquisa. Daí a insistência em dois pontos: a data local não pode
ser deslocada, e um lote nunca pode ser gravado pela metade.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from src.models.activity import Activity
from src.models.registro_historico import RegistroHistorico
from src.models.strava_token import StravaToken
from src.repositories.activity_repository import (
    ORIGEM_MANUAL,
    ORIGEM_STRAVA,
    ActivityRepository,
    ResultadoGravacao,
)
from src.repositories.corredor_state_repository import CorredorStateRepository
from src.services import database_service
from src.services.database_service import SCHEMA_VERSION, DatabaseService
from src.utils.errors import StateError

AGORA = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

# Corrida das 22h de 01/01 no fuso do atleta — que já é 02/01 em UTC. O par é
# escolhido assim de propósito: qualquer confusão entre os dois campos aparece
# como mudança de DIA, e dia errado é linha errada da planilha.
LOCAL = datetime(2026, 1, 1, 22, 15, 30)  # noqa: DTZ001 — local e ingênuo, como o modelo
UTC_DA_MESMA = datetime(2026, 1, 2, 1, 15, 30, tzinfo=UTC)


@pytest.fixture
def banco(tmp_path):
    """Banco isolado em `tmp_path`, fechado no fim.

    Fechar não é higiene opcional: no Windows, um arquivo SQLite ainda aberto
    faz a limpeza do `tmp_path` falhar com `PermissionError` de forma
    intermitente e confusa.
    """
    servico = DatabaseService(tmp_path / "estado.db")
    servico.init_schema()
    try:
        yield servico
    finally:
        servico.close()


@pytest.fixture
def repo(banco):
    return ActivityRepository(banco.connect(), agora=lambda: AGORA)


def _atividade(**overrides) -> Activity:
    padrao = {
        "id": 111,
        "name": "Corrida da manhã",
        "date": LOCAL,
        "distance_m": 10250.5,
        "moving_time_s": 3300,
        "elapsed_time_s": 3450,
        "type": "Run",
        "average_speed": 3.1,
        "average_heartrate": 152.4,
        "max_heartrate": 178.0,
        "elevation_gain": 88.0,
        "calories": None,
        "cadence": 84.5,
        "start_date_utc": UTC_DA_MESMA,
    }
    return Activity(**{**padrao, **overrides})


def _em(dia: str, hora: int = 8) -> datetime:
    """Horário local ingênuo num dia dado, para montar cenários de agenda."""
    return datetime.fromisoformat(dia).replace(hour=hora)


# ----------------------------------------------------------------- migração


def test_init_schema_cria_a_tabela_de_atividades(tmp_path):
    servico = DatabaseService(tmp_path / "sub" / "estado.db")
    try:
        servico.init_schema()
        conn = servico.connect()

        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        conn.execute("SELECT corredor_id FROM activities")
    finally:
        servico.close()


def test_migracao_de_v1_preserva_o_estado(monkeypatch, tmp_path):
    """Recriar `corredor_state` obrigaria 50+ participantes a reautorizar.

    Cada um deles teria de receber um link novo, por mensagem, um por um.
    """
    caminho = tmp_path / "estado.db"

    monkeypatch.setattr(database_service, "SCHEMA_VERSION", 1)
    antigo = DatabaseService(caminho)
    antigo.init_schema()
    CorredorStateRepository(antigo.connect()).salvar_token(
        "p001",
        StravaToken(
            access_token="access-1",
            refresh_token="refresh-1",
            expira_em=AGORA + timedelta(hours=6),
            athlete_id=777,
            scope="read,activity:read_all",
        ),
    )
    antigo.close()
    monkeypatch.undo()

    novo = DatabaseService(caminho)
    try:
        novo.init_schema()
        estado = CorredorStateRepository(novo.connect()).buscar("p001")

        assert novo.connect().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert estado.refresh_token == "refresh-1"
        assert estado.athlete_id == 777
        novo.connect().execute("SELECT corredor_id FROM activities")
    finally:
        novo.close()


def test_init_schema_e_idempotente_no_v2(banco, repo):
    repo.salvar("p001", _atividade())

    banco.init_schema()
    banco.init_schema()

    assert len(repo.por_dia("p001", date(2026, 1, 1))) == 1


# --------------------------------------------------------------- ida e volta


def test_salvar_e_recuperar_preserva_os_campos(repo):
    repo.salvar("p001", _atividade())

    (lida,) = repo.por_dia("p001", date(2026, 1, 1))
    assert lida == _atividade()


def test_opcionais_ausentes_sobrevivem_como_none(repo):
    """Correr sem cinta de FC é normal; a ausência tem de voltar como ausência."""
    repo.salvar(
        "p001",
        _atividade(average_heartrate=None, max_heartrate=None, cadence=None, elevation_gain=None),
    )

    (lida,) = repo.por_dia("p001", date(2026, 1, 1))
    assert lida.average_heartrate is None
    assert lida.max_heartrate is None
    assert lida.cadence is None
    assert lida.elevation_gain is None


def test_data_local_volta_ingenua_e_sem_deslocamento(repo):
    """22h de 01/01 continua 22h de 01/01.

    Passar esse valor pelo conversor de UTC o converteria e a corrida mudaria de
    DIA — ou seja, de linha da planilha.
    """
    repo.salvar("p001", _atividade())

    (lida,) = repo.por_dia("p001", date(2026, 1, 1))
    assert lida.date == LOCAL
    assert lida.date.tzinfo is None
    assert lida.day == date(2026, 1, 1)


def test_dia_local_e_instante_utc_sao_coisas_diferentes(banco, repo):
    """A mesma corrida cai em 01/01 local e 02/01 UTC — e as duas são usadas."""
    repo.salvar("p001", _atividade())

    linha = banco.connect().execute(
        "SELECT day, start_date_utc FROM activities WHERE corredor_id = 'p001'"
    ).fetchone()
    assert linha["day"] == "2026-01-01"
    assert linha["start_date_utc"].startswith("2026-01-02")


def test_metros_e_segundos_sao_gravados_sem_conversao(banco, repo):
    """Converter para km na escrita duplicaria a conversão que o modelo já faz."""
    repo.salvar("p001", _atividade())

    linha = banco.connect().execute(
        "SELECT distance_m, moving_time_s FROM activities WHERE corredor_id = 'p001'"
    ).fetchone()
    assert linha["distance_m"] == 10250.5
    assert linha["moving_time_s"] == 3300


def test_datas_sao_gravadas_como_texto(banco, repo):
    """Os adaptadores de `datetime` do sqlite3 estão depreciados no 3.12."""
    repo.salvar("p001", _atividade())

    linha = banco.connect().execute(
        "SELECT date_local, start_date_utc, criado_em FROM activities"
    ).fetchone()
    assert isinstance(linha["date_local"], str)
    assert isinstance(linha["start_date_utc"], str)
    assert isinstance(linha["criado_em"], str)


# --------------------------------------------------------------------- upsert


def test_atividade_editada_atualiza_em_vez_de_duplicar(repo):
    """A busca refaz 2 dias a cada execução; "pula se já existe" perderia a correção.

    O atleta corrige a distância depois do upload e a carga do dia ficaria
    errada para sempre.
    """
    repo.salvar("p001", _atividade(distance_m=10000.0))

    resultado = repo.salvar("p001", _atividade(distance_m=10500.0))

    (lida,) = repo.por_dia("p001", date(2026, 1, 1))
    assert lida.distance_m == 10500.0
    assert (resultado.novas, resultado.atualizadas) == (0, 1)


def test_primeira_gravacao_conta_como_nova(repo):
    resultado = repo.salvar("p001", _atividade())

    assert (resultado.novas, resultado.atualizadas, resultado.total) == (1, 0, 1)


def test_upsert_preserva_calorias_que_a_listagem_nao_traz(repo):
    """`/athlete/activities` nunca devolve `calories` — sobrescrever apagaria."""
    repo.salvar("p001", _atividade(calories=612.0))

    repo.salvar("p001", _atividade(calories=None))

    (lida,) = repo.por_dia("p001", date(2026, 1, 1))
    assert lida.calories == 612.0


def test_upsert_sobrescreve_frequencia_cardiaca_removida(repo):
    """Aqui `None` é "correu sem cinta", não "não perguntamos".

    Blindar com COALESCE tornaria impossível corrigir um valor errado.
    """
    repo.salvar("p001", _atividade(average_heartrate=152.4))

    repo.salvar("p001", _atividade(average_heartrate=None))

    (lida,) = repo.por_dia("p001", date(2026, 1, 1))
    assert lida.average_heartrate is None


def test_upsert_preserva_criado_em_e_avanca_atualizado_em(banco):
    """`criado_em` é a única prova de que a corrida foi editada depois."""
    momentos = iter([AGORA, AGORA + timedelta(days=1)])
    repo = ActivityRepository(banco.connect(), agora=lambda: next(momentos))

    repo.salvar("p001", _atividade())
    repo.salvar("p001", _atividade(distance_m=11000.0))

    linha = banco.connect().execute(
        "SELECT criado_em, atualizado_em FROM activities"
    ).fetchone()
    assert linha["criado_em"].startswith("2026-08-18")
    assert linha["atualizado_em"].startswith("2026-08-19")


def test_mesma_atividade_de_corredores_diferentes_nao_colide(repo):
    """Uma chave global faria a corrida de um participante ser dada a outro.

    Acontece se duas inscrições apontarem para a mesma conta do Strava.
    """
    repo.salvar("p001", _atividade(id=999))
    repo.salvar("p002", _atividade(id=999))

    assert len(repo.por_dia("p001", date(2026, 1, 1))) == 1
    assert len(repo.por_dia("p002", date(2026, 1, 1))) == 1
    assert repo.existe("p001", 999) is True
    assert repo.existe("p002", 999) is True


def test_dono_da_corrida_nunca_muda(banco, repo):
    repo.salvar("p001", _atividade(id=999))
    repo.salvar("p002", _atividade(id=999))

    donos = banco.connect().execute(
        "SELECT corredor_id FROM activities WHERE activity_id = 999 ORDER BY corredor_id"
    ).fetchall()
    assert [linha["corredor_id"] for linha in donos] == ["p001", "p002"]


def test_dias_afetados_inclui_o_dia_antigo_quando_a_corrida_muda_de_data(repo, caplog):
    """Sem isso, o dia antigo ficaria com carga fantasma e nada apontaria para lá."""
    repo.salvar("p001", _atividade(date=_em("2026-01-01")))

    with caplog.at_level("WARNING"):
        resultado = repo.salvar("p001", _atividade(date=_em("2026-01-05")))

    assert resultado.dias_afetados == frozenset({date(2026, 1, 1), date(2026, 1, 5)})
    assert "mudou de 2026-01-01 para 2026-01-05" in caplog.text
    assert repo.por_dia("p001", date(2026, 1, 1)) == ()


def test_dias_afetados_de_gravacao_normal_traz_so_o_dia_da_corrida(repo):
    resultado = repo.salvar_muitas(
        "p001",
        [_atividade(id=1, date=_em("2026-01-01")), _atividade(id=2, date=_em("2026-01-03"))],
    )

    assert resultado.dias_afetados == frozenset({date(2026, 1, 1), date(2026, 1, 3)})


def test_origem_padrao_e_strava_e_o_upsert_nao_a_reescreve(banco, repo):
    """Uma sincronização não pode reetiquetar em silêncio uma linha curada à mão."""
    repo.salvar("p001", _atividade(), origem=ORIGEM_MANUAL)

    repo.salvar("p001", _atividade(distance_m=11000.0))

    origem = banco.connect().execute("SELECT origem FROM activities").fetchone()["origem"]
    assert origem == ORIGEM_MANUAL
    assert ORIGEM_STRAVA == "strava"


# ------------------------------------------------------------------ consultas


def test_por_dia_traz_as_duas_corridas_do_dia(repo):
    """Se a segunda corrida chega numa execução posterior, agregar só o retorno
    da API escreveria a carga apenas dela."""
    repo.salvar("p001", _atividade(id=1, date=_em("2026-01-01", hora=7)))
    repo.salvar("p001", _atividade(id=2, date=_em("2026-01-01", hora=19)))

    assert len(repo.por_dia("p001", date(2026, 1, 1))) == 2


def test_por_periodo_inclui_os_dois_extremos(repo):
    """A grade da planilha é inclusiva: o Dia 1 e o último dia contam."""
    for dia in ("2026-01-01", "2026-01-05", "2026-01-10"):
        repo.salvar("p001", _atividade(id=int(dia[-2:]), date=_em(dia)))

    achadas = repo.por_periodo("p001", date(2026, 1, 1), date(2026, 1, 10))

    assert len(achadas) == 3


def test_por_periodo_exclui_o_que_esta_fora(repo):
    repo.salvar("p001", _atividade(id=1, date=_em("2026-01-01")))
    repo.salvar("p001", _atividade(id=2, date=_em("2026-02-01")))

    achadas = repo.por_periodo("p001", date(2026, 1, 1), date(2026, 1, 31))

    assert [a.id for a in achadas] == [1]


def test_por_periodo_ignora_atividades_de_outro_corredor(repo):
    repo.salvar("p001", _atividade(id=1))
    repo.salvar("p002", _atividade(id=2))

    achadas = repo.por_periodo("p001", date(2026, 1, 1), date(2026, 12, 31))

    assert [a.id for a in achadas] == [1]


def test_por_periodo_ordena_por_dia_e_hora(repo):
    repo.salvar("p001", _atividade(id=3, date=_em("2026-01-02", hora=6)))
    repo.salvar("p001", _atividade(id=2, date=_em("2026-01-01", hora=19)))
    repo.salvar("p001", _atividade(id=1, date=_em("2026-01-01", hora=7)))

    achadas = repo.por_periodo("p001", date(2026, 1, 1), date(2026, 1, 31))

    assert [a.id for a in achadas] == [1, 2, 3]


def test_por_periodo_de_intervalo_invertido_e_vazio(repo):
    repo.salvar("p001", _atividade())

    assert repo.por_periodo("p001", date(2026, 1, 10), date(2026, 1, 1)) == ()


def test_por_periodo_sem_dados_e_vazio(repo):
    assert repo.por_periodo("p999", date(2026, 1, 1), date(2026, 12, 31)) == ()


def test_existe_e_por_corredor(repo):
    repo.salvar("p001", _atividade(id=111))

    assert repo.existe("p001", 111) is True
    assert repo.existe("p002", 111) is False
    assert repo.existe("p001", 222) is False


# --------------------------------------------------------------- marca d'água


def test_ultimo_evento_em_devolve_o_maior_instante_utc(repo):
    repo.salvar("p001", _atividade(id=1, start_date_utc=datetime(2026, 1, 2, 1, 0, tzinfo=UTC)))
    repo.salvar("p001", _atividade(id=2, start_date_utc=datetime(2026, 3, 9, 6, 0, tzinfo=UTC)))
    repo.salvar("p001", _atividade(id=3, start_date_utc=datetime(2026, 2, 1, 6, 0, tzinfo=UTC)))

    assert repo.ultimo_evento_em("p001") == datetime(2026, 3, 9, 6, 0, tzinfo=UTC)


def test_ultimo_evento_em_sem_atividades_e_none(repo):
    assert repo.ultimo_evento_em("p999") is None


def test_marca_dagua_e_por_corredor(repo):
    repo.salvar("p001", _atividade(start_date_utc=datetime(2026, 5, 1, 6, 0, tzinfo=UTC)))

    assert repo.ultimo_evento_em("p002") is None


def test_historico_manual_nao_empurra_a_marca_dagua(repo):
    """Empurrar o `after=` com dado digitado à mão puliria corridas reais."""
    repo.salvar("p001", _atividade(id=1, start_date_utc=datetime(2026, 1, 2, 1, 0, tzinfo=UTC)))
    repo.salvar(
        "p001",
        _atividade(id=2, date=_em("2026-06-01"), start_date_utc=None),
        origem=ORIGEM_MANUAL,
    )

    assert repo.ultimo_evento_em("p001") == datetime(2026, 1, 2, 1, 0, tzinfo=UTC)


# -------------------------------------------------------------------- Fase 5.5


def test_atividades_manuais_sem_activity_id_convivem(repo):
    """O UNIQUE trata cada NULL como distinto — é o que a Fase 5.5 precisa.

    O outro lado da moeda: o upsert não dispara nelas, então reimportar o
    histórico duplicaria tudo. Daí a chave própria prevista para a Fase 5.5.
    """
    repo.salvar("p001", _atividade(id=None, date=_em("2026-01-01")), origem=ORIGEM_MANUAL)
    repo.salvar("p001", _atividade(id=None, date=_em("2026-01-02")), origem=ORIGEM_MANUAL)

    achadas = repo.por_periodo("p001", date(2026, 1, 1), date(2026, 1, 31))

    assert len(achadas) == 2
    assert all(a.id is None for a in achadas)


def test_importar_historico_grava_so_dias_com_corrida(repo):
    """Um dia de descanso não é uma atividade — nem quando vem do Strava."""
    resultado = repo.importar_historico(
        "p001",
        [
            RegistroHistorico(day=date(2026, 1, 1), carga_km=10.0, tempo_total_s=3000),
            RegistroHistorico(day=date(2026, 1, 2), carga_km=0.0, tempo_total_s=None),
        ],
    )

    assert resultado == ResultadoGravacao(1, 0, frozenset({date(2026, 1, 1)}))
    achadas = repo.por_periodo("p001", date(2026, 1, 1), date(2026, 1, 31))
    assert len(achadas) == 1
    assert achadas[0].day == date(2026, 1, 1)


def test_importar_historico_grava_distancia_e_tempo(repo):
    repo.importar_historico(
        "p001", [RegistroHistorico(day=date(2026, 1, 1), carga_km=10.5, tempo_total_s=3200)]
    )

    achada = repo.por_dia("p001", date(2026, 1, 1))[0]

    assert achada.id is None
    assert achada.distance_m == pytest.approx(10500.0)
    assert achada.moving_time_s == 3200
    assert achada.elapsed_time_s == 3200
    assert achada.start_date_utc is None


def test_importar_historico_e_idempotente(repo):
    """Rodar a adoção duas vezes atualiza a linha, não duplica."""
    registro = RegistroHistorico(day=date(2026, 1, 1), carga_km=10.0, tempo_total_s=3000)

    primeiro = repo.importar_historico("p001", [registro])
    segundo = repo.importar_historico(
        "p001", [RegistroHistorico(day=date(2026, 1, 1), carga_km=12.0, tempo_total_s=3600)]
    )

    assert primeiro == ResultadoGravacao(1, 0, frozenset({date(2026, 1, 1)}))
    assert segundo == ResultadoGravacao(0, 1, frozenset({date(2026, 1, 1)}))
    achadas = repo.por_periodo("p001", date(2026, 1, 1), date(2026, 1, 31))
    assert len(achadas) == 1
    assert achadas[0].distance_m == pytest.approx(12000.0)


def test_reimportar_nao_colide_com_atividade_do_strava_no_mesmo_dia(repo):
    """O índice parcial não conflita com o UNIQUE (corredor_id, activity_id) da v2."""
    repo.salvar("p001", _atividade(id=1, date=_em("2026-01-01")))

    repo.importar_historico(
        "p001", [RegistroHistorico(day=date(2026, 1, 1), carga_km=5.0, tempo_total_s=1500)]
    )

    achadas = repo.por_dia("p001", date(2026, 1, 1))
    assert len(achadas) == 2
    assert {a.id for a in achadas} == {1, None}


def test_tempo_desconhecido_vira_zero_e_loga(repo, caplog):
    with caplog.at_level("WARNING"):
        repo.importar_historico(
            "p001", [RegistroHistorico(day=date(2026, 1, 1), carga_km=8.0, tempo_total_s=None)]
        )

    achada = repo.por_dia("p001", date(2026, 1, 1))[0]
    assert achada.moving_time_s == 0
    assert "sem tempo conhecido" in caplog.text


def test_importar_historico_vazio_nao_toca_o_banco(repo):
    assert repo.importar_historico("p001", []) == ResultadoGravacao(0, 0)


def test_importar_so_dias_de_descanso_nao_grava_nada(repo):
    resultado = repo.importar_historico(
        "p001", [RegistroHistorico(day=date(2026, 1, 1), carga_km=0.0, tempo_total_s=0)]
    )

    assert resultado == ResultadoGravacao(0, 0)
    assert repo.por_dia("p001", date(2026, 1, 1)) == ()


# ---------------------------------------------------------------------- falhas


def test_lote_vazio_nao_toca_no_banco(repo):
    resultado = repo.salvar_muitas("p001", [])

    assert (resultado.novas, resultado.atualizadas) == (0, 0)
    assert resultado.dias_afetados == frozenset()


def test_lote_com_linha_invalida_nao_grava_nada(repo):
    """Meia gravação + marca d'água avançada = corridas perdidas para sempre."""
    lote = [
        _atividade(id=1, date=_em("2026-01-01")),
        _atividade(id=2, date=_em("2026-01-02"), distance_m=-5.0),  # viola o CHECK
        _atividade(id=3, date=_em("2026-01-03")),
    ]

    with pytest.raises(StateError, match="gravar as atividades"):
        repo.salvar_muitas("p001", lote)

    assert repo.por_periodo("p001", date(2026, 1, 1), date(2026, 12, 31)) == ()


def test_erro_no_lote_nao_deixa_transacao_aberta(banco, repo):
    """Senão o `commit()` do corredor seguinte gravaria as linhas parciais deste."""
    with pytest.raises(StateError):
        repo.salvar_muitas("p001", [_atividade(distance_m=-1.0)])

    assert banco.connect().in_transaction is False

    repo.salvar("p002", _atividade(id=42))
    assert repo.existe("p001", 111) is False


def test_falha_do_sqlite_vira_state_error(banco, repo):
    banco.connect().execute("DROP TABLE activities")

    with pytest.raises(StateError, match="atividades"):
        repo.existe("p001", 111)


def test_data_local_corrompida_levanta_em_vez_de_sumir_com_a_corrida(banco, repo):
    """Divergência consciente com o `corredor_state`.

    Lá, uma data ilegível vira `None` e custa uma página de API. Aqui, virar
    `None` faria a corrida desaparecer da agregação e subnotificar a carga do dia
    sem nenhum sinal.
    """
    repo.salvar("p001", _atividade())
    banco.connect().execute("UPDATE activities SET date_local = 'ontem'")

    with pytest.raises(StateError, match="horário local inválido"):
        repo.por_dia("p001", date(2026, 1, 1))
