"""Testes da política de autorização (Fase 3).

O que se testa aqui não é HTTP, é a política: de quem é o token, qual escopo foi
concedido, e quando o participante precisa ser procurado de novo. Errar isso com
50+ participantes significa atribuir o token de um ao cadastro de outro, ou
descobrir meses depois que a coleta de alguém nunca funcionou.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.api.rate_limiter import RateLimiter
from src.api.strava_client import StravaClient
from src.models.corredor import Corredor
from src.repositories.corredor_state_repository import CorredorStateRepository
from src.services.database_service import DatabaseService
from src.utils.auth import (
    ESCOPO_EXIGIDO,
    REDIRECT_URI,
    RetornoDaAutorizacao,
    chamar_renovando,
    inscrever,
    ler_retorno,
    obter_access_token,
    refresh_token_corrente,
    url_de_autorizacao,
    validar_escopo,
)
from src.utils.errors import (
    CodeExchangeError,
    InsufficientScopeError,
    RevokedTokenError,
    UnexpectedAthleteError,
    UnverifiedScopeError,
)

URL_API = "https://api.exemplo/v3"
URL_OAUTH = "https://oauth.exemplo/token"

ESCOPO_COMPLETO = "read,activity:read_all"

URL_DE_RETORNO = (
    "http://localhost/exchange_token?state=p001&code=abc123&scope=read,activity:read_all"
)


class _Servidor:
    def __init__(self, *respostas: httpx.Response) -> None:
        self.respostas = list(respostas)
        self.requisicoes: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requisicoes.append(request)
        if not self.respostas:
            raise AssertionError(f"requisição inesperada: {request.url}")
        return self.respostas.pop(0)


def _token_json(*, athlete_id: int | None = 777, expira_em_horas: int = 6) -> dict:
    expira = datetime.now(UTC) + timedelta(hours=expira_em_horas)
    corpo = {
        "access_token": "access-novo",
        "refresh_token": "refresh-novo",
        "expires_at": int(expira.timestamp()),
    }
    if athlete_id is not None:
        corpo["athlete"] = {"id": athlete_id}
    return corpo


def _resposta(status: int = 200, *, json=None) -> httpx.Response:
    return httpx.Response(status, json=json if json is not None else {})


def _erro_strava(status: int, recurso: str) -> httpx.Response:
    return _resposta(status, json={"errors": [{"resource": recurso, "code": "invalid"}]})


def _corredor(identificador: str = "p001", *, refresh_token: str = "token-do-toml") -> Corredor:
    return Corredor(
        id=identificador,
        nome="Ana",
        refresh_token=refresh_token,
        start_date=date(2026, 1, 1),
        cutover_date=date(2026, 1, 1),
        excel_path=Path(f"{identificador}.xlsx"),
    )


def _cliente(servidor: _Servidor) -> StravaClient:
    return StravaClient(
        "12345",
        "segredo",
        limiter=RateLimiter(pausa_s=0, dormir=lambda _: None),
        http=httpx.Client(transport=httpx.MockTransport(servidor)),
        url_api=URL_API,
        url_oauth=URL_OAUTH,
    )


@pytest.fixture
def repo(tmp_path):
    servico = DatabaseService(tmp_path / "estado.db")
    servico.init_schema()
    try:
        yield CorredorStateRepository(servico.connect())
    finally:
        servico.close()


# --------------------------------------------------------- link de autorização


def test_url_de_autorizacao_tem_tudo_que_o_strava_exige():
    url = url_de_autorizacao("12345", "p001")

    campos = parse_qs(urlparse(url).query)
    assert campos["client_id"] == ["12345"]
    assert campos["redirect_uri"] == [REDIRECT_URI]
    assert campos["scope"] == [ESCOPO_EXIGIDO]
    assert campos["response_type"] == ["code"]


def test_state_carrega_o_id_do_corredor():
    """Com 50+ participantes, é o que impede atribuir o token de um a outro."""
    campos = parse_qs(urlparse(url_de_autorizacao("12345", "p042")).query)

    assert campos["state"] == ["p042"]


# ------------------------------------------------------------ leitura da URL


def test_le_url_completa():
    retorno = ler_retorno(URL_DE_RETORNO)

    assert retorno.code == "abc123"
    assert retorno.state == "p001"
    assert retorno.scope == ESCOPO_COMPLETO


@pytest.mark.parametrize(
    "colado",
    [
        URL_DE_RETORNO,
        f"  {URL_DE_RETORNO}\n",
        f"<{URL_DE_RETORNO}>",
        f'"{URL_DE_RETORNO}"',
        URL_DE_RETORNO.removeprefix("http://"),  # o participante tirou o esquema
        URL_DE_RETORNO.replace("?", "?\n"),      # quebra de linha do app de mensagem
    ],
)
def test_le_url_suja_como_o_participante_manda(colado):
    """É o que a pessoa tem à mão — exigir higiene dela geraria retrabalho."""
    assert ler_retorno(colado).code == "abc123"


def test_le_code_cru():
    retorno = ler_retorno("abc123")

    assert retorno.code == "abc123"
    assert retorno.scope is None  # e por isso a inscrição vai recusar


def test_recusa_de_autorizacao_e_explicita():
    with pytest.raises(CodeExchangeError, match="recusou"):
        ler_retorno("http://localhost/exchange_token?state=p001&error=access_denied")


def test_url_sem_code_diz_o_que_pedir():
    with pytest.raises(CodeExchangeError, match="não tem `code`"):
        ler_retorno("http://localhost/exchange_token?state=p001&scope=read")


def test_texto_vazio_e_erro():
    with pytest.raises(CodeExchangeError, match="nada foi colado"):
        ler_retorno("   ")


# ------------------------------------------------------------------- escopo


def test_escopo_completo_passa():
    validar_escopo(ESCOPO_COMPLETO, corredor_id="p001")


def test_escopo_reduzido_e_recusado():
    """A tela do Strava permite aprovar desmarcando o acesso às atividades."""
    with pytest.raises(InsufficientScopeError, match="activity:read_all"):
        validar_escopo("read", corredor_id="p001")


def test_escopo_ausente_e_recusado_por_padrao():
    """Colar só o `code` esconde o escopo; aceitar seria descobrir meses depois."""
    with pytest.raises(UnverifiedScopeError, match="URL inteira"):
        validar_escopo(None, corredor_id="p001")


def test_escopo_ausente_pode_ser_forcado(caplog):
    with caplog.at_level("WARNING"):
        validar_escopo(None, corredor_id="p001", permitir_nao_verificado=True)

    assert "não verificado" in caplog.text


# ---------------------------------------------------------------- inscrição


def test_inscricao_grava_token_atleta_e_escopo(repo):
    servidor = _Servidor(_resposta(json=_token_json()))

    estado = inscrever(_corredor(), ler_retorno(URL_DE_RETORNO), client=_cliente(servidor), repo=repo)

    assert estado.refresh_token == "refresh-novo"
    assert estado.athlete_id == 777
    assert estado.scope == ESCOPO_COMPLETO
    assert estado.inscrito is True


def test_escopo_e_conferido_antes_de_gastar_o_code(repo):
    """O `code` é de uso único: gastá-lo para depois recusar exigiria outro link."""
    servidor = _Servidor()  # qualquer requisição aqui é falha do teste

    with pytest.raises(InsufficientScopeError):
        inscrever(
            _corredor(),
            RetornoDaAutorizacao(code="abc123", state="p001", scope="read"),
            client=_cliente(servidor),
            repo=repo,
        )

    assert servidor.requisicoes == []


def test_conta_diferente_na_reinscricao_e_recusada(repo):
    """A URL pode ter vindo da pessoa errada — atribuir errado é o pior erro possível."""
    inscrever(
        _corredor(),
        ler_retorno(URL_DE_RETORNO),
        client=_cliente(_Servidor(_resposta(json=_token_json(athlete_id=777)))),
        repo=repo,
    )

    with pytest.raises(UnexpectedAthleteError, match="888"):
        inscrever(
            _corredor(),
            ler_retorno(URL_DE_RETORNO),
            client=_cliente(_Servidor(_resposta(json=_token_json(athlete_id=888)))),
            repo=repo,
        )

    assert repo.buscar("p001").athlete_id == 777


def test_conta_diferente_pode_ser_forcada(repo):
    inscrever(
        _corredor(),
        ler_retorno(URL_DE_RETORNO),
        client=_cliente(_Servidor(_resposta(json=_token_json(athlete_id=777)))),
        repo=repo,
    )

    inscrever(
        _corredor(),
        ler_retorno(URL_DE_RETORNO),
        client=_cliente(_Servidor(_resposta(json=_token_json(athlete_id=888)))),
        repo=repo,
        forcar_atleta=True,
    )

    assert repo.buscar("p001").athlete_id == 888


# -------------------------------------------------- precedência do refresh token


@pytest.mark.parametrize(
    ("tem_estado", "esperado", "origem"),
    [
        (False, "token-do-toml", "cadastro"),
        (True, "refresh-novo", "SQLite"),
    ],
)
def test_precedencia_do_refresh_token(repo, tem_estado, esperado, origem):
    """O TOML só faz o bootstrap; depois da primeira renovação, o SQLite manda."""
    if tem_estado:
        inscrever(
            _corredor(),
            ler_retorno(URL_DE_RETORNO),
            client=_cliente(_Servidor(_resposta(json=_token_json()))),
            repo=repo,
        )

    assert refresh_token_corrente(_corredor(), repo.buscar("p001")) == (esperado, origem)


def test_primeira_renovacao_usa_o_token_do_cadastro(repo):
    servidor = _Servidor(_resposta(json=_token_json()))

    token = obter_access_token(_corredor(), client=_cliente(servidor), repo=repo)

    assert token == "access-novo"
    assert b"token-do-toml" in servidor.requisicoes[0].content
    # E o token novo já está gravado: o do TOML pode ter sido invalidado.
    assert repo.buscar("p001").refresh_token == "refresh-novo"


def test_access_token_valido_nao_gasta_requisicao(repo):
    """A cota é da aplicação: reexecutar logo depois não pode recobrar todo mundo."""
    obter_access_token(
        _corredor(), client=_cliente(_Servidor(_resposta(json=_token_json()))), repo=repo
    )

    servidor = _Servidor()  # qualquer requisição aqui é falha do teste
    token = obter_access_token(_corredor(), client=_cliente(servidor), repo=repo)

    assert token == "access-novo"
    assert servidor.requisicoes == []


def test_access_token_perto_de_expirar_e_renovado(repo):
    obter_access_token(
        _corredor(),
        client=_cliente(_Servidor(_resposta(json=_token_json(expira_em_horas=0)))),
        repo=repo,
    )

    servidor = _Servidor(_resposta(json=_token_json()))
    obter_access_token(_corredor(), client=_cliente(servidor), repo=repo)

    assert len(servidor.requisicoes) == 1


def test_sem_refresh_token_manda_inscrever(repo):
    with pytest.raises(RevokedTokenError, match="inscricao link p001"):
        obter_access_token(
            _corredor(refresh_token=""), client=_cliente(_Servidor()), repo=repo
        )


# ------------------------------------------------------------------ revogação


def test_refresh_recusado_marca_reinscricao(repo):
    servidor = _Servidor(_erro_strava(400, "RefreshToken"))

    with pytest.raises(RevokedTokenError, match="link novo"):
        obter_access_token(_corredor(), client=_cliente(servidor), repo=repo)

    estado = repo.buscar("p001")
    assert estado.precisa_reinscricao is True
    assert estado.inscrito is False


def test_corredor_marcado_e_pulado_sem_gastar_requisicao(repo):
    """Insistir num token morto todo dia tira cota de quem está ativo."""
    repo.marcar_reinscricao("p001", "revogado")
    servidor = _Servidor()

    with pytest.raises(RevokedTokenError, match="reinscrição"):
        obter_access_token(_corredor(), client=_cliente(servidor), repo=repo)

    assert servidor.requisicoes == []


def test_inscricao_reabilita_corredor_marcado(repo):
    repo.marcar_reinscricao("p001", "revogado")

    inscrever(
        _corredor(),
        ler_retorno(URL_DE_RETORNO),
        client=_cliente(_Servidor(_resposta(json=_token_json()))),
        repo=repo,
    )

    assert repo.buscar("p001").precisa_reinscricao is False


# --------------------------------------------------------- 401: renovar e repetir


def test_401_renova_e_repete_uma_vez(repo):
    """O access token dura 6h; um 401 é rotina, não motivo para tirar da pesquisa."""
    servidor = _Servidor(
        _resposta(json=_token_json()),          # renovação inicial
        _resposta(401, json={}),                # a chamada dá 401
        _resposta(json=_token_json()),          # renovação forçada
        _resposta(json=[{"id": 1}]),            # a repetição funciona
    )
    cliente = _cliente(servidor)

    resultado = chamar_renovando(
        _corredor(),
        lambda token: cliente.get_activities(token, after=0),
        client=cliente,
        repo=repo,
    )

    assert resultado == [{"id": 1}]
    assert repo.buscar("p001").precisa_reinscricao is False


def test_401_repetido_e_revogacao(repo):
    servidor = _Servidor(
        _resposta(json=_token_json()),
        _resposta(401, json={}),
        _resposta(json=_token_json()),
        _resposta(401, json={}),
    )
    cliente = _cliente(servidor)

    with pytest.raises(RevokedTokenError, match="mesmo após renovar"):
        chamar_renovando(
            _corredor(),
            lambda token: cliente.get_activities(token, after=0),
            client=cliente,
            repo=repo,
        )

    assert repo.buscar("p001").precisa_reinscricao is True


def test_segredos_nao_vazam_no_log(repo, caplog):
    servidor = _Servidor(_resposta(json=_token_json()))

    with caplog.at_level("DEBUG"):
        obter_access_token(
            _corredor(refresh_token="token-secretissimo"), client=_cliente(servidor), repo=repo
        )

    assert "token-secretissimo" not in caplog.text
    assert "access-novo" not in caplog.text
