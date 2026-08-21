"""Cliente HTTP da API do Strava.

O cliente é **stateless quanto a token**: todo método de API recebe o
`access_token` de fora. Quem decide *qual* token usar é o `utils/auth.py`. Isso
permite que uma única instância — e portanto um único `RateLimiter` — sirva
todos os participantes da pesquisa, que é o que o limite por aplicação exige.

Toda requisição passa pelo mesmo `_request`, **inclusive as de `/oauth/token`**:
elas também consomem a cota e também devolvem os cabeçalhos de vazão.

Os erros do Strava são classificados pelo **corpo**, não só pelo status. Um 400
pode ser um `code` expirado (peça um link novo ao participante) ou um refresh
token revogado (o participante precisa reautorizar) — tratar os dois igual
tiraria gente da pesquisa por engano.
"""
from __future__ import annotations

from collections.abc import Iterator
from types import TracebackType
from typing import Any, Self

import httpx

from src.api.rate_limiter import MAX_TENTATIVAS_429, RateLimiter
from src.models.strava_token import StravaToken
from src.utils.errors import (
    ExpiredAccessTokenError,
    ExpiredCodeError,
    InvalidResponseError,
    NetworkError,
    RateLimitError,
    RevokedTokenError,
    StravaError,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

URL_AUTORIZACAO = "https://www.strava.com/oauth/authorize"
URL_OAUTH = "https://www.strava.com/oauth/token"
URL_API = "https://www.strava.com/api/v3"

# 200 é o máximo aceito pelo Strava — quanto maior a página, menos requisições.
PER_PAGE = 200
# Trava contra paginação infinita se a API passar a repetir páginas.
MAX_PAGINAS = 20


class StravaClient:
    """Encapsula as chamadas HTTP à API do Strava."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        limiter: RateLimiter,
        http: httpx.Client | None = None,
        timeout_s: float = 20.0,
        url_api: str = URL_API,
        url_oauth: str = URL_OAUTH,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._limiter = limiter
        self._url_api = url_api.rstrip("/")
        self._url_oauth = url_oauth
        self._proprio = http is None
        self._http = http or httpx.Client(timeout=timeout_s)

    def __repr__(self) -> str:
        # Sem client_id nem secret: este objeto aparece em traceback.
        return f"<StravaClient api={self._url_api}>"

    # ------------------------------------------------------------------ OAuth

    def exchange_code(self, code: str, *, scope: str | None = None) -> StravaToken:
        """Troca o `code` da autorização por um par de tokens.

        Args:
            code: valor de uso único que o participante nos devolveu.
            scope: escopo lido da URL de retorno — o Strava não o devolve aqui.
        """
        payload = self._oauth(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "grant_type": "authorization_code",
            }
        )
        return StravaToken.from_payload(payload, scope=scope)

    def refresh_access_token(self, refresh_token: str) -> StravaToken:
        """Renova o access token a partir do refresh token corrente."""
        payload = self._oauth(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        )
        return StravaToken.from_payload(payload)

    # -------------------------------------------------------------------- API

    def get_athlete(self, access_token: str) -> dict:
        """Dados do atleta autenticado — usado para conferir de quem é o token."""
        dados = self._api("/athlete", access_token)
        if not isinstance(dados, dict):
            raise InvalidResponseError("resposta de /athlete não é um objeto JSON")
        return dados

    def get_activities(self, access_token: str, *, after: int) -> list[dict]:
        """Todas as atividades a partir de `after`, já paginadas.

        Args:
            after: instante em epoch (segundos). O Strava filtra por
                `start_date` em **UTC**, não pelo horário local do atleta.
        """
        return list(self.iter_activities(access_token, after=after))

    def iter_activities(self, access_token: str, *, after: int) -> Iterator[dict]:
        """Percorre as páginas de atividades, uma atividade por vez."""
        for pagina in range(1, MAX_PAGINAS + 1):
            lote = self._api(
                "/athlete/activities",
                access_token,
                params={"after": after, "page": pagina, "per_page": PER_PAGE},
            )
            if not isinstance(lote, list):
                raise InvalidResponseError(
                    f"resposta de /athlete/activities (página {pagina}) não é uma lista"
                )

            yield from lote

            # Página incompleta significa fim: evita uma requisição a mais por
            # corredor, que com 50+ participantes vira cota desperdiçada.
            if len(lote) < PER_PAGE:
                return

        raise InvalidResponseError(
            f"paginação passou de {MAX_PAGINAS} páginas ({MAX_PAGINAS * PER_PAGE} atividades) — "
            "interrompida por segurança"
        )

    # --------------------------------------------------------------- interno

    def close(self) -> None:
        """Fecha o cliente HTTP, se ele for nosso."""
        if self._proprio:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        erro: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _oauth(self, dados: dict[str, str]) -> dict:
        """POST em `/oauth/token`, classificando as falhas de autorização."""
        resposta = self._request("POST", self._url_oauth, data=dados)
        payload = _json(resposta)
        if not isinstance(payload, dict):
            raise InvalidResponseError("resposta de /oauth/token não é um objeto JSON")
        return payload

    def _api(self, caminho: str, access_token: str, params: dict | None = None) -> Any:
        """GET autenticado na API, devolvendo o JSON já decodificado."""
        resposta = self._request(
            "GET",
            f"{self._url_api}{caminho}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return _json(resposta)

    def _request(self, metodo: str, url: str, **kwargs) -> httpx.Response:
        """Faz a requisição respeitando a vazão e traduzindo os erros.

        O 429 é a única situação em que repetimos aqui: o 401 é decidido uma
        camada acima (`auth`), que sabe se vale renovar o token ou se o
        participante precisa reautorizar.
        """
        for tentativa in range(1, MAX_TENTATIVAS_429 + 1):
            self._limiter.antes_da_chamada()

            try:
                resposta = self._http.request(metodo, url, **kwargs)
            except httpx.TransportError as erro:
                raise NetworkError(f"falha de rede ao chamar o Strava: {erro}") from erro

            self._limiter.registrar_resposta(resposta.headers)
            logger.debug("%s %s -> %d (%s)", metodo, url, resposta.status_code, self._limiter.resumo)

            if resposta.status_code == httpx.codes.TOO_MANY_REQUESTS:
                if tentativa == MAX_TENTATIVAS_429:
                    break
                self._limiter.esperar_apos_429(tentativa, resposta.headers)
                continue

            if resposta.is_success:
                return resposta

            _levantar_erro(resposta)

        raise RateLimitError(
            f"o Strava respondeu 429 em {MAX_TENTATIVAS_429} tentativas seguidas; "
            "reduza STRAVA_PAUSA_ENTRE_CHAMADAS_S ou rode com menos corredores por vez"
        )


def _levantar_erro(resposta: httpx.Response) -> None:
    """Traduz uma resposta de erro do Strava na exceção certa.

    A distinção entre `code` morto e refresh token revogado sai do **corpo**: os
    dois chegam como 400, e confundir os dois manda o operador para o caminho
    errado de recuperação.
    """
    detalhe = _detalhe_do_erro(resposta)
    recurso = detalhe.get("resource", "")
    codigo = detalhe.get("code", "")

    if resposta.status_code == httpx.codes.BAD_REQUEST:
        if recurso == "AuthorizationCode":
            raise ExpiredCodeError(
                "o código de autorização já foi usado ou expirou — gere um link novo "
                "(`python -m src.inscricao link <id>`) e peça ao participante para autorizar de novo"
            )
        if recurso == "RefreshToken":
            raise RevokedTokenError(
                "o refresh token não é mais válido (revogado no Strava ou substituído) — "
                "o participante precisa autorizar de novo"
            )

    if resposta.status_code == httpx.codes.UNAUTHORIZED:
        # Rotineiro: o access token dura 6 horas. Quem chamou renova e repete.
        raise ExpiredAccessTokenError(
            f"o Strava recusou o access token (401{_sufixo(recurso, codigo)})"
        )

    if resposta.is_server_error:
        raise InvalidResponseError(
            f"o Strava respondeu {resposta.status_code} — indisponibilidade temporária"
        )

    raise StravaError(
        f"o Strava respondeu {resposta.status_code}{_sufixo(recurso, codigo)}"
    )


def _detalhe_do_erro(resposta: httpx.Response) -> dict[str, str]:
    """Primeiro item de `errors[]` do corpo de erro, ou vazio se não der para ler."""
    try:
        corpo = resposta.json()
    except ValueError:
        return {}
    if not isinstance(corpo, dict):
        return {}
    erros = corpo.get("errors")
    if isinstance(erros, list) and erros and isinstance(erros[0], dict):
        return {str(chave): str(valor) for chave, valor in erros[0].items()}
    return {}


def _sufixo(recurso: str, codigo: str) -> str:
    return f": {recurso}/{codigo}" if recurso or codigo else ""


def _json(resposta: httpx.Response) -> Any:
    """Decodifica o corpo, transformando JSON quebrado em erro nosso."""
    try:
        return resposta.json()
    except ValueError as erro:
        raise InvalidResponseError(f"o Strava devolveu um corpo que não é JSON: {erro}") from erro
