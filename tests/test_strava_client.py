"""Testes do cliente HTTP do Strava (Fase 3).

Nenhum teste toca a rede: o `httpx.MockTransport` intercepta tudo, e o handler
guarda as requisições para que as asserções sejam feitas sobre o que realmente
saiu — sem `unittest.mock`.
"""
from __future__ import annotations

import httpx
import pytest

from src.api.rate_limiter import MAX_TENTATIVAS_429, RateLimiter
from src.api.strava_client import MAX_PAGINAS, PER_PAGE, StravaClient, montar_cliente
from src.utils.errors import (
    ExpiredAccessTokenError,
    ExpiredCodeError,
    InvalidResponseError,
    NetworkError,
    RateLimitError,
    RevokedTokenError,
    StravaError,
)

URL_API = "https://api.exemplo/v3"
URL_OAUTH = "https://oauth.exemplo/token"

TOKEN_OK = {
    "access_token": "access-novo",
    "refresh_token": "refresh-novo",
    "expires_at": 1_800_000_000,
    "athlete": {"id": 777},
}

VAZAO = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "12,140"}


class _Servidor:
    """Fila de respostas + registro das requisições que chegaram."""

    def __init__(self, *respostas: httpx.Response) -> None:
        self.respostas = list(respostas)
        self.requisicoes: list[httpx.Request] = []
        self.erro: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requisicoes.append(request)
        if self.erro is not None:
            raise self.erro
        if not self.respostas:
            raise AssertionError(f"requisição inesperada: {request.method} {request.url}")
        return self.respostas.pop(0)


def _resposta(status: int = 200, *, json=None, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=json if json is not None else {}, headers={**VAZAO, **(headers or {})})


def _erro_strava(status: int, recurso: str, codigo: str = "invalid") -> httpx.Response:
    return _resposta(
        status,
        json={"message": "Bad Request", "errors": [{"resource": recurso, "field": "x", "code": codigo}]},
    )


def _corrida(identificador: int) -> dict:
    return {"id": identificador, "type": "Run", "distance": 5000.0}


def _cliente(servidor: _Servidor, *, dormir=None) -> StravaClient:
    limitador = RateLimiter(pausa_s=0, dormir=dormir or (lambda _: None))
    return StravaClient(
        "12345",
        "segredo-do-app",
        limiter=limitador,
        http=httpx.Client(transport=httpx.MockTransport(servidor)),
        url_api=URL_API,
        url_oauth=URL_OAUTH,
    )


# ------------------------------------------------------------------- OAuth


def test_troca_do_code_devolve_token_e_atleta():
    servidor = _Servidor(_resposta(json=TOKEN_OK))

    token = _cliente(servidor).exchange_code("code-123", scope="read,activity:read_all")

    assert token.access_token == "access-novo"
    assert token.athlete_id == 777
    # O escopo não vem na resposta do Strava — chega pela URL de retorno.
    assert token.scope == "read,activity:read_all"

    enviado = servidor.requisicoes[0]
    assert enviado.method == "POST"
    assert b"grant_type=authorization_code" in enviado.content
    assert b"code-123" in enviado.content


def test_renovacao_usa_o_refresh_token():
    servidor = _Servidor(_resposta(json=TOKEN_OK))

    token = _cliente(servidor).refresh_access_token("refresh-antigo")

    assert token.refresh_token == "refresh-novo"
    assert b"grant_type=refresh_token" in servidor.requisicoes[0].content
    assert b"refresh-antigo" in servidor.requisicoes[0].content


def test_code_expirado_diz_o_que_fazer():
    """O `code` é de uso único e viaja por mensagem — este é o erro mais comum."""
    servidor = _Servidor(_erro_strava(400, "AuthorizationCode"))

    with pytest.raises(ExpiredCodeError, match="link novo"):
        _cliente(servidor).exchange_code("code-velho")


def test_refresh_token_revogado_e_distinto_do_code():
    servidor = _Servidor(_erro_strava(400, "RefreshToken"))

    with pytest.raises(RevokedTokenError, match="autorizar de novo"):
        _cliente(servidor).refresh_access_token("refresh-morto")


def test_refresh_token_com_code_diferente_de_invalid_nao_e_revogacao():
    """A distinção do CLAUDE.md é o PAR `resource`/`code`, não só o `resource`
    — um 400 de RefreshToken por outro motivo não pode marcar o participante
    para reinscrição por engano (achado do code review da Fase 8)."""
    servidor = _Servidor(_erro_strava(400, "RefreshToken", codigo="outro-motivo"))

    with pytest.raises(StravaError) as excinfo:
        _cliente(servidor).refresh_access_token("refresh-1")

    assert not isinstance(excinfo.value, RevokedTokenError)


def test_resposta_de_token_incompleta_e_erro():
    servidor = _Servidor(_resposta(json={"access_token": "só-isso"}))

    with pytest.raises(InvalidResponseError, match="refresh_token"):
        _cliente(servidor).refresh_access_token("refresh-1")


# --------------------------------------------------------------------- API


def test_busca_atividades_manda_after_e_autorizacao():
    servidor = _Servidor(_resposta(json=[_corrida(1)]))

    _cliente(servidor).get_activities("access-1", after=1_767_225_600)

    enviado = servidor.requisicoes[0]
    assert enviado.url.params["after"] == "1767225600"
    assert enviado.url.params["per_page"] == str(PER_PAGE)
    assert enviado.headers["Authorization"] == "Bearer access-1"


def test_paginacao_percorre_ate_a_pagina_incompleta():
    cheia = [_corrida(i) for i in range(PER_PAGE)]
    servidor = _Servidor(
        _resposta(json=cheia),
        _resposta(json=cheia),
        _resposta(json=[_corrida(9001), _corrida(9002)]),
    )

    atividades = _cliente(servidor).get_activities("access-1", after=0)

    assert len(atividades) == PER_PAGE * 2 + 2
    assert len(servidor.requisicoes) == 3
    assert [r.url.params["page"] for r in servidor.requisicoes] == ["1", "2", "3"]


def test_pagina_incompleta_encerra_sem_requisicao_extra():
    """Uma requisição a mais por corredor vira cota desperdiçada com 50+ deles."""
    servidor = _Servidor(_resposta(json=[_corrida(1)]))

    _cliente(servidor).get_activities("access-1", after=0)

    assert len(servidor.requisicoes) == 1


def test_pagina_vazia_encerra():
    servidor = _Servidor(_resposta(json=[]))

    assert _cliente(servidor).get_activities("access-1", after=0) == []


def test_paginacao_infinita_e_interrompida():
    cheia = [_corrida(i) for i in range(PER_PAGE)]
    servidor = _Servidor(*[_resposta(json=cheia) for _ in range(MAX_PAGINAS + 1)])

    with pytest.raises(InvalidResponseError, match="paginação"):
        _cliente(servidor).get_activities("access-1", after=0)


def test_total_multiplo_exato_de_max_paginas_nao_e_confundido_com_infinita():
    """Uma página de confirmação decide se a última página cheia era, por
    coincidência, a última mesmo — sem ela, um backlog de exatamente
    `MAX_PAGINAS * PER_PAGE` atividades abortaria por engano (achado do code
    review da Fase 8)."""
    cheia = [_corrida(i) for i in range(PER_PAGE)]
    respostas = [_resposta(json=cheia) for _ in range(MAX_PAGINAS)] + [_resposta(json=[])]
    servidor = _Servidor(*respostas)

    atividades = _cliente(servidor).get_activities("access-1", after=0)

    assert len(atividades) == MAX_PAGINAS * PER_PAGE
    assert len(servidor.requisicoes) == MAX_PAGINAS + 1  # inclui a página de confirmação


def test_resposta_de_atividades_fora_do_formato():
    servidor = _Servidor(_resposta(json={"message": "erro"}))

    with pytest.raises(InvalidResponseError, match="não é uma lista"):
        _cliente(servidor).get_activities("access-1", after=0)


def test_busca_atleta():
    servidor = _Servidor(_resposta(json={"id": 777, "firstname": "Ana"}))

    assert _cliente(servidor).get_athlete("access-1")["id"] == 777


# ------------------------------------------------------------------- erros


def test_401_e_access_token_vencido_nao_revogacao():
    """O access token dura 6h; tratar isso como revogação tiraria gente da coleta."""
    servidor = _Servidor(_resposta(401, json={}))

    with pytest.raises(ExpiredAccessTokenError):
        _cliente(servidor).get_athlete("access-velho")


def test_erro_de_servidor_e_indisponibilidade():
    servidor = _Servidor(_resposta(500, json={}))

    with pytest.raises(InvalidResponseError, match="500"):
        _cliente(servidor).get_athlete("access-1")


def test_erro_inesperado_ainda_e_erro_do_strava():
    servidor = _Servidor(_resposta(404, json={}))

    with pytest.raises(StravaError, match="404"):
        _cliente(servidor).get_athlete("access-1")


def test_falha_de_rede_vira_network_error():
    servidor = _Servidor()
    servidor.erro = httpx.ConnectTimeout("timeout simulado")

    with pytest.raises(NetworkError, match="rede"):
        _cliente(servidor).get_athlete("access-1")


def test_corpo_que_nao_e_json():
    servidor = _Servidor(httpx.Response(200, text="<html>manutenção</html>", headers=VAZAO))

    with pytest.raises(InvalidResponseError, match="não é JSON"):
        _cliente(servidor).get_athlete("access-1")


# -------------------------------------------------------------------- vazão


def test_429_e_repetido_apos_espera():
    dormidas: list[float] = []
    servidor = _Servidor(_resposta(429, json={}), _resposta(json={"id": 777}))

    atleta = _cliente(servidor, dormir=dormidas.append).get_athlete("access-1")

    assert atleta["id"] == 777
    assert len(servidor.requisicoes) == 2
    assert dormidas  # esperou antes de repetir


def test_429_persistente_vira_rate_limit_error():
    servidor = _Servidor(*[_resposta(429, json={}) for _ in range(MAX_TENTATIVAS_429)])

    with pytest.raises(RateLimitError, match="429"):
        _cliente(servidor, dormir=lambda _: None).get_athlete("access-1")

    assert len(servidor.requisicoes) == MAX_TENTATIVAS_429


class _LimitadorContado(RateLimiter):
    """Limitador real, só que contando quantas vezes foi consultado."""

    def __init__(self) -> None:
        super().__init__(pausa_s=0, dormir=lambda _: None)
        self.chamadas = 0
        self.respostas = 0

    def antes_da_chamada(self) -> None:
        self.chamadas += 1
        super().antes_da_chamada()

    def registrar_resposta(self, headers) -> None:
        self.respostas += 1
        super().registrar_resposta(headers)


def test_limitador_ve_todas_as_chamadas_inclusive_oauth():
    """A renovação de token também consome a cota da aplicação.

    Deixar o `/oauth/token` fora do limitador subestimaria o gasto em ~50% — é
    uma requisição por corredor, todo dia.
    """
    servidor = _Servidor(_resposta(json=TOKEN_OK), _resposta(json={"id": 777}))
    limitador = _LimitadorContado()
    cliente = StravaClient(
        "12345",
        "segredo-do-app",
        limiter=limitador,
        http=httpx.Client(transport=httpx.MockTransport(servidor)),
        url_api=URL_API,
        url_oauth=URL_OAUTH,
    )

    cliente.refresh_access_token("refresh-1")
    cliente.get_athlete("access-1")

    assert limitador.chamadas == 2
    assert limitador.respostas == 2
    # O uso vem dos cabeçalhos da resposta, não de um contador local.
    assert "12/100" in limitador.resumo


def test_segredos_nao_aparecem_no_repr_nem_no_log(caplog):
    servidor = _Servidor(_resposta(json=TOKEN_OK))
    cliente = _cliente(servidor)

    with caplog.at_level("DEBUG"):
        cliente.refresh_access_token("refresh-secretissimo")

    assert "segredo-do-app" not in repr(cliente)
    assert "12345" not in repr(cliente)
    assert "refresh-secretissimo" not in caplog.text
    assert "access-novo" not in caplog.text


# ---------------------------------------------------------------- montar_cliente


def test_montar_cliente_fia_client_id_e_timeout():
    """Ponto único de fiação cliente+limitador, reaproveitado por `main.py` e
    `inscricao.py` (achado do code review da Fase 8: os dois montavam isso
    cada um por si)."""
    cliente = montar_cliente(
        "12345", "segredo-do-app", timeout_s=5.0, pausa_s=0.0, reserva=10
    )

    assert "segredo-do-app" not in repr(cliente)
    assert "12345" not in repr(cliente)
