"""Testes da hierarquia de exceções (`src/utils/errors.py`, Fase 8).

O módulo nunca importa nada do projeto de propósito — `auth` e `strava_client`
precisam levantar os mesmos erros sem depender um do outro. O que se verifica
aqui é a hierarquia em si (quem é filha de quem) e os atributos que carregam
contexto de diagnóstico, hoje só exercitados incidentalmente por outros testes.
"""
from __future__ import annotations

from src.utils.errors import (
    AuthorizationError,
    CodeExchangeError,
    ExpiredCodeError,
    GradeDesalinhadaError,
    InsufficientScopeError,
    PlanilhaError,
    QuotaExhaustedError,
    RevokedTokenError,
    StateError,
    StravaError,
    StravaSyncError,
    UnexpectedAthleteError,
)


def test_stravasyncerror_e_a_raiz_e_herda_runtimeerror():
    assert issubclass(StravaSyncError, RuntimeError)
    assert issubclass(StravaError, StravaSyncError)
    assert issubclass(StateError, StravaSyncError)
    assert issubclass(PlanilhaError, StravaSyncError)


def test_quota_exhausted_error_e_irma_nao_filha_de_authorization_error():
    """A distinção governa o `main.py`: cota não é falha de um corredor específico."""
    assert issubclass(QuotaExhaustedError, StravaError)
    assert not issubclass(QuotaExhaustedError, AuthorizationError)
    assert not issubclass(AuthorizationError, QuotaExhaustedError)


def test_stateerror_e_planilhaerror_nao_sao_aparentadas_entre_si():
    assert not issubclass(StateError, PlanilhaError)
    assert not issubclass(PlanilhaError, StateError)


def test_authorization_error_str_sem_corredor_id_nao_adiciona_prefixo():
    erro = AuthorizationError("token inválido")

    assert str(erro) == "token inválido"
    assert erro.corredor_id is None


def test_authorization_error_str_com_corredor_id_usa_prefixo():
    erro = AuthorizationError("token inválido", corredor_id="p001")

    assert str(erro) == "corredor p001: token inválido"


def test_insufficient_scope_error_guarda_concedido_e_exigido():
    erro = InsufficientScopeError(
        "escopo insuficiente", concedido="read", exigido="activity:read_all", corredor_id="p001"
    )

    assert erro.concedido == "read"
    assert erro.exigido == "activity:read_all"
    assert erro.corredor_id == "p001"
    assert "p001" in str(erro)


def test_insufficient_scope_error_e_authorization_error():
    assert issubclass(InsufficientScopeError, AuthorizationError)


def test_unexpected_athlete_error_guarda_esperado_e_recebido():
    erro = UnexpectedAthleteError(
        "conta divergente", esperado=111, recebido=222, corredor_id="p001"
    )

    assert erro.esperado == 111
    assert erro.recebido == 222


def test_unexpected_athlete_error_str_herda_o_prefixo_do_corredor_id():
    erro = UnexpectedAthleteError("conta divergente", esperado=111, recebido=222, corredor_id="p001")

    assert str(erro) == "corredor p001: conta divergente"


def test_expired_code_error_e_subclasse_de_codeexchangeerror():
    assert issubclass(ExpiredCodeError, CodeExchangeError)
    assert issubclass(CodeExchangeError, AuthorizationError)


def test_revoked_token_error_e_authorization_error_sem_atributos_extras():
    erro = RevokedTokenError("revogado", corredor_id="p001")

    assert isinstance(erro, AuthorizationError)
    assert erro.corredor_id == "p001"
    assert not hasattr(erro, "concedido")
    assert not hasattr(erro, "esperado")


def test_grade_desalinhada_error_e_planilha_error():
    assert issubclass(GradeDesalinhadaError, PlanilhaError)
