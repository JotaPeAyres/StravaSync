"""Testes do banco de estado por corredor (Fase 3).

O estado aqui é o que o `corredores.toml` não pode guardar: o token corrente, o
atleta que autorizou e até onde a coleta chegou.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.models.strava_token import StravaToken
from src.repositories.corredor_state_repository import CorredorStateRepository
from src.services.database_service import SCHEMA_VERSION, DatabaseService, de_texto, para_texto
from src.utils.errors import StateError

AGORA = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


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
    return CorredorStateRepository(banco.connect(), agora=lambda: AGORA)


def _token(**overrides) -> StravaToken:
    padrao = {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expira_em": AGORA + timedelta(hours=6),
        "athlete_id": 777,
        "scope": "read,activity:read_all",
    }
    return StravaToken(**{**padrao, **overrides})


def test_init_schema_cria_tabela_e_versao(tmp_path):
    servico = DatabaseService(tmp_path / "sub" / "estado.db")
    try:
        servico.init_schema()
        conn = servico.connect()

        assert (tmp_path / "sub" / "estado.db").is_file()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        conn.execute("SELECT corredor_id FROM corredor_state")
    finally:
        servico.close()


def test_init_schema_e_idempotente(banco, repo):
    """Rodar de novo não pode apagar o que já foi coletado."""
    repo.salvar_token("p001", _token())

    banco.init_schema()
    banco.init_schema()

    assert repo.buscar("p001") is not None


def test_corredor_sem_estado_devolve_none(repo):
    assert repo.buscar("p999") is None


def test_salvar_token_grava_e_atualiza_sem_duplicar(repo):
    repo.salvar_token("p001", _token())
    repo.salvar_token("p001", _token(access_token="access-2", refresh_token="refresh-2"))

    estados = repo.listar()
    assert len(estados) == 1
    assert estados[0].refresh_token == "refresh-2"
    assert estados[0].access_token == "access-2"
    assert estados[0].atualizado_em == AGORA


def test_renovacao_nao_apaga_atleta_nem_escopo(repo):
    """A renovação responde sem `athlete_id` e sem `scope`.

    Gravar `NULL` neles destruiria a prova de qual escopo o participante
    concedeu — o dado que evita ter de procurar a pessoa de novo meses depois.
    """
    repo.salvar_token("p001", _token())

    repo.salvar_token("p001", _token(athlete_id=None, scope=None, refresh_token="refresh-2"))

    estado = repo.buscar("p001")
    assert estado.athlete_id == 777
    assert estado.scope == "read,activity:read_all"
    assert estado.refresh_token == "refresh-2"


def test_datas_sobrevivem_a_ida_e_volta(repo):
    expira = AGORA + timedelta(hours=6)
    repo.salvar_token("p001", _token(expira_em=expira))

    estado = repo.buscar("p001")
    assert estado.access_token_expira_em == expira
    assert estado.access_token_expira_em.tzinfo is not None


def test_datas_sao_gravadas_como_texto(banco, repo):
    """Os adaptadores de `datetime` do sqlite3 estão depreciados no 3.12."""
    repo.salvar_token("p001", _token())

    bruto = banco.connect().execute(
        "SELECT access_token_expira_em FROM corredor_state WHERE corredor_id = 'p001'"
    ).fetchone()[0]
    assert isinstance(bruto, str)


def test_registrar_sincronizacao_guarda_as_duas_datas(repo):
    evento = datetime(2026, 8, 17, 22, 30, tzinfo=UTC)
    repo.registrar_sincronizacao("p001", ultimo_evento_em=evento)

    estado = repo.buscar("p001")
    assert estado.ultima_sincronizacao == AGORA
    assert estado.ultimo_evento_em == evento


def test_ultimo_evento_nunca_retrocede(repo):
    """Retroceder faria a execução seguinte repaginar histórico já visto."""
    recente = datetime(2026, 8, 17, 22, 30, tzinfo=UTC)
    antigo = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)

    repo.registrar_sincronizacao("p001", ultimo_evento_em=recente)
    repo.registrar_sincronizacao("p001", ultimo_evento_em=antigo)

    assert repo.buscar("p001").ultimo_evento_em == recente


def test_sincronizacao_sem_corridas_preserva_o_evento(repo):
    """Uma execução em que ninguém correu não pode zerar a base do `after=`."""
    evento = datetime(2026, 8, 17, 22, 30, tzinfo=UTC)
    repo.registrar_sincronizacao("p001", ultimo_evento_em=evento)

    repo.registrar_sincronizacao("p001", ultimo_evento_em=None)

    assert repo.buscar("p001").ultimo_evento_em == evento


def test_marcar_e_limpar_reinscricao(repo):
    repo.salvar_token("p001", _token())

    repo.marcar_reinscricao("p001", "refresh token revogado")
    estado = repo.buscar("p001")
    assert estado.precisa_reinscricao is True
    assert estado.motivo_reinscricao == "refresh token revogado"
    assert estado.inscrito is False
    # O access token guardado morre junto: insistir nele só gastaria cota.
    assert estado.access_token == ""

    repo.limpar_reinscricao("p001")
    assert repo.buscar("p001").precisa_reinscricao is False


def test_marcar_reinscricao_de_corredor_novo_nao_falha(repo):
    """A marcação pode chegar antes de qualquer token (bootstrap inválido)."""
    repo.marcar_reinscricao("p001", "token do cadastro nunca funcionou")

    assert repo.buscar("p001").precisa_reinscricao is True


def test_salvar_token_desfaz_a_reinscricao(repo):
    """Se a troca funcionou, o participante não está mais revogado."""
    repo.marcar_reinscricao("p001", "revogado")

    repo.salvar_token("p001", _token())

    estado = repo.buscar("p001")
    assert estado.precisa_reinscricao is False
    assert estado.inscrito is True


def test_segredos_nao_aparecem_no_repr(repo):
    repo.salvar_token("p001", _token())

    texto = repr(repo.buscar("p001"))
    assert "refresh-1" not in texto
    assert "access-1" not in texto


@pytest.mark.parametrize("margem", [300, 0])
def test_access_token_expirado_nao_e_reaproveitado(repo, margem):
    repo.salvar_token("p001", _token(expira_em=datetime.now(UTC) - timedelta(minutes=1)))

    assert repo.buscar("p001").token_de_acesso_utilizavel(margem) is None


def test_access_token_valido_e_reaproveitado(repo):
    repo.salvar_token("p001", _token(expira_em=datetime.now(UTC) + timedelta(hours=2)))

    assert repo.buscar("p001").token_de_acesso_utilizavel() == "access-1"


def test_conversao_de_datas_ingenuas_assume_utc():
    ingenuo = datetime(2026, 1, 1, 10, 0)  # noqa: DTZ001 — o caso que queremos cobrir

    assert de_texto(para_texto(ingenuo)) == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def test_data_corrompida_no_banco_nao_derruba_a_leitura(banco, repo):
    """Um valor estranho no banco vira ausência, não exceção no meio da coleta."""
    repo.salvar_token("p001", _token())
    banco.connect().execute(
        "UPDATE corredor_state SET ultima_sincronizacao = 'ontem' WHERE corredor_id = 'p001'"
    )

    assert repo.buscar("p001").ultima_sincronizacao is None


def test_registrar_falha_comeca_em_um_e_incrementa(repo):
    assert repo.registrar_falha("p001") == 1
    assert repo.registrar_falha("p001") == 2
    assert repo.registrar_falha("p001") == 3

    assert repo.buscar("p001").falhas_consecutivas == 3


def test_registrar_falha_nao_mexe_em_outro_corredor(repo):
    repo.registrar_falha("p001")
    repo.registrar_falha("p001")

    assert repo.buscar("p002") is None
    assert repo.registrar_falha("p002") == 1


def test_sincronizacao_bem_sucedida_zera_as_falhas(repo):
    """O alerta é sobre falhas *seguidas* — um sucesso interrompe a sequência."""
    repo.registrar_falha("p001")
    repo.registrar_falha("p001")

    repo.registrar_sincronizacao("p001")

    assert repo.buscar("p001").falhas_consecutivas == 0


def test_salvar_token_nao_mexe_nas_falhas(repo):
    """Trocar/renovar o token não é o mesmo que sincronizar com sucesso."""
    repo.registrar_falha("p001")
    repo.registrar_falha("p001")

    repo.salvar_token("p001", _token())

    assert repo.buscar("p001").falhas_consecutivas == 2


def test_falha_do_sqlite_vira_state_error(tmp_path):
    servico = DatabaseService(tmp_path / "estado.db")
    try:
        servico.init_schema()
        conn = servico.connect()
        conn.execute("DROP TABLE corredor_state")
        repo = CorredorStateRepository(conn)

        with pytest.raises(StateError, match="estado dos corredores"):
            repo.buscar("p001")
    finally:
        servico.close()
