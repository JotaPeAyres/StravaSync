"""Política de autorização: quem pode falar com o Strava em nome de quem.

A inscrição é **manual (opção A)**: enviamos ao participante um link, ele
autoriza, e nos devolve o que aparece na barra de endereço. Não há servidor nem
página hospedada — a página de retorno nem carrega, e não precisa: o `code` está
na URL.

Três detalhes fazem isso funcionar com 50+ participantes:

- **`state = <id do corredor>`** na URL de autorização. O Strava devolve o
  `state` intacto, então a URL colada se auto-identifica. Sem isso, atribuir o
  token de um participante ao cadastro de outro seria questão de tempo.
- **Aceitamos a URL inteira colada**, não só o `code`. Além de ser o que a pessoa
  tem à mão, é a **única** fonte do escopo concedido (ver abaixo).
- **O `code` é de uso único e de vida curta.** Como ele viaja por mensagem, a
  troca tem de acontecer assim que chega.

⚠️ **O escopo não vem na resposta da troca.** O Strava responde apenas com
tokens, expiração e atleta; `scope` aparece só na query da URL de retorno. Como
a tela de consentimento tem caixas de seleção — dá para aprovar desmarcando o
acesso a atividades privadas —, quem cola só o `code` não traz a informação que
precisamos conferir. Por isso a inscrição **recusa** nesse caso, em vez de
descobrir meses depois e ter de procurar a pessoa de novo.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from src.api.strava_client import URL_AUTORIZACAO, StravaClient
from src.models.corredor import Corredor
from src.models.estado_corredor import EstadoCorredor
from src.repositories.corredor_state_repository import CorredorStateRepository
from src.utils.errors import (
    CodeExchangeError,
    ExpiredAccessTokenError,
    InsufficientScopeError,
    RevokedTokenError,
    UnexpectedAthleteError,
    UnverifiedScopeError,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# O *Authorization Callback Domain* do app registrado no Strava é `localhost`.
# A página não carrega — e não precisa. Esta constante é única de propósito: se
# a URL do link e a da troca divergirem, o Strava recusa de um jeito difícil de
# diagnosticar.
REDIRECT_URI = "http://localhost/exchange_token"

# Trocar de escopo obriga **todos** os participantes a reautorizar; por isso não
# é configurável.
ESCOPO_EXIGIDO = "activity:read_all"

# Um token que expira em menos que isto não vale a pena usar: entre a decisão e
# a requisição chegar ao Strava passa tempo.
MARGEM_DO_ACCESS_TOKEN_S = 300


@dataclass(frozen=True)
class RetornoDaAutorizacao:
    """O que o participante nos devolveu depois de autorizar."""

    code: str
    state: str | None = None
    scope: str | None = None


def url_de_autorizacao(
    client_id: str, corredor_id: str, *, redirect_uri: str = REDIRECT_URI
) -> str:
    """Monta o link que o participante abre para autorizar a pesquisa."""
    parametros = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "force",
        "scope": ESCOPO_EXIGIDO,
        "state": corredor_id,
    }
    return f"{URL_AUTORIZACAO}?{urlencode(parametros)}"


def ler_retorno(texto_colado: str) -> RetornoDaAutorizacao:
    """Extrai `code`, `state` e `scope` do que o participante mandou.

    Aceita a URL inteira (com ou sem esquema), com espaços e quebras de linha de
    aplicativo de mensagem, entre aspas ou `<>`, ou o `code` cru.

    Raises:
        CodeExchangeError: se o texto não contiver um `code` utilizável.
    """
    texto = texto_colado.strip().strip("<>\"'").replace("\n", "").replace(" ", "")
    if not texto:
        raise CodeExchangeError("nada foi colado — cole a URL inteira da barra de endereço")

    if "?" not in texto and "&" not in texto:
        # É o `code` cru: a inscrição vai recusar por escopo não verificado.
        return RetornoDaAutorizacao(code=texto)

    consulta = urlparse(texto if "://" in texto else f"http://{texto}").query
    campos = parse_qs(consulta)

    if erro := _primeiro(campos, "error"):
        if erro == "access_denied":
            raise CodeExchangeError(
                "o participante recusou a autorização no Strava — envie um link novo "
                "e explique que o acesso às atividades é necessário para a pesquisa"
            )
        raise CodeExchangeError(f"o Strava devolveu erro na autorização: {erro}")

    code = _primeiro(campos, "code")
    if not code:
        raise CodeExchangeError(
            "a URL colada não tem `code` — peça a URL completa da barra de endereço "
            "depois de aprovar (ela começa com http://localhost/exchange_token?)"
        )

    return RetornoDaAutorizacao(
        code=code,
        state=_primeiro(campos, "state"),
        scope=_primeiro(campos, "scope"),
    )


def validar_escopo(
    scope: str | None, *, corredor_id: str, permitir_nao_verificado: bool = False
) -> None:
    """Confere se o participante concedeu `activity:read_all`.

    Raises:
        UnverifiedScopeError: se o escopo não veio (colaram só o `code`).
        InsufficientScopeError: se veio, mas sem o acesso que a pesquisa precisa.
    """
    if not scope:
        if permitir_nao_verificado:
            logger.warning(
                "Escopo de %s não verificado: só o `code` foi colado. Se o participante "
                "tiver desmarcado o acesso às atividades privadas, isso só aparecerá na "
                "primeira coleta vazia.",
                corredor_id,
            )
            return
        raise UnverifiedScopeError(
            "não deu para verificar o escopo concedido: o Strava não o devolve na troca, "
            "ele vem só na URL de retorno. Peça a URL inteira da barra de endereço "
            "(ou use --sem-verificar-escopo, por sua conta e risco)",
            corredor_id=corredor_id,
        )

    concedidos = {parte.strip() for parte in scope.split(",") if parte.strip()}
    if ESCOPO_EXIGIDO not in concedidos:
        raise InsufficientScopeError(
            f"o participante autorizou apenas {scope!r}, sem {ESCOPO_EXIGIDO!r} — "
            "provavelmente desmarcou o acesso às atividades na tela do Strava; "
            "envie um link novo e peça para deixar a caixa marcada",
            concedido=scope,
            exigido=ESCOPO_EXIGIDO,
            corredor_id=corredor_id,
        )


def inscrever(
    corredor: Corredor,
    retorno: RetornoDaAutorizacao,
    *,
    client: StravaClient,
    repo: CorredorStateRepository,
    permitir_nao_verificado: bool = False,
    forcar_atleta: bool = False,
) -> EstadoCorredor:
    """Troca o `code` por tokens e grava o estado do participante.

    O escopo é conferido **antes** da troca: o `code` é de uso único, e gastá-lo
    para descobrir que o escopo era insuficiente obrigaria a gerar outro link de
    qualquer jeito.

    Raises:
        UnverifiedScopeError, InsufficientScopeError: ver `validar_escopo`.
        UnexpectedAthleteError: se a conta que autorizou não é a já registrada.
        ExpiredCodeError: se o `code` já foi usado ou expirou.
    """
    validar_escopo(
        retorno.scope,
        corredor_id=corredor.id,
        permitir_nao_verificado=permitir_nao_verificado,
    )

    token = client.exchange_code(retorno.code, scope=retorno.scope)

    anterior = repo.buscar(corredor.id)
    if (
        not forcar_atleta
        and anterior is not None
        and anterior.athlete_id is not None
        and token.athlete_id is not None
        and anterior.athlete_id != token.athlete_id
    ):
        raise UnexpectedAthleteError(
            f"a autorização é da conta Strava {token.athlete_id}, mas este corredor já "
            f"estava associado à conta {anterior.athlete_id} — confira se a URL veio da "
            "pessoa certa antes de usar --forcar-atleta",
            esperado=anterior.athlete_id,
            recebido=token.athlete_id,
            corredor_id=corredor.id,
        )

    repo.salvar_token(corredor.id, token, scope=retorno.scope)
    logger.info(
        "Corredor %s inscrito (atleta Strava %s, escopo %s).",
        corredor,
        token.athlete_id if token.athlete_id is not None else "?",
        token.scope or "não verificado",
    )

    estado = repo.buscar(corredor.id)
    if estado is None:  # pragma: no cover - o upsert acabou de gravar
        raise RevokedTokenError("o estado não foi gravado", corredor_id=corredor.id)
    return estado


def refresh_token_corrente(corredor: Corredor, estado: EstadoCorredor | None) -> tuple[str, str]:
    """Decide qual refresh token usar, e diz de onde ele veio.

    Esta é a **única** definição de precedência do projeto: o SQLite manda
    quando tem token; o `corredores.toml` só faz o bootstrap da primeira
    execução. Consequência a lembrar no suporte: depois da primeira renovação
    bem-sucedida, editar o token no TOML não tem efeito nenhum — reinscreva pelo
    `python -m src.inscricao trocar ...`.
    """
    if estado is not None and estado.refresh_token:
        return estado.refresh_token, "SQLite"
    return corredor.refresh_token, "cadastro"


def obter_access_token(
    corredor: Corredor,
    *,
    client: StravaClient,
    repo: CorredorStateRepository,
    forcar_renovacao: bool = False,
) -> str:
    """Devolve um access token válido para este corredor, renovando se preciso.

    Args:
        forcar_renovacao: ignora o token guardado e renova. Usado quando o
            Strava já recusou o token em cache — insistir nele daria 401 de novo.

    Raises:
        RevokedTokenError: se o participante precisa autorizar de novo.
    """
    estado = repo.buscar(corredor.id)

    if estado is not None and estado.precisa_reinscricao:
        # Sem gastar requisição: com a cota dividida por 50+ participantes,
        # insistir num token morto todo dia tira coleta de quem está ativo.
        raise RevokedTokenError(
            f"marcado para reinscrição ({estado.motivo_reinscricao or 'motivo não registrado'}) — "
            f"gere um link novo: python -m src.inscricao link {corredor.id}",
            corredor_id=corredor.id,
        )

    if estado is not None and not forcar_renovacao:
        guardado = estado.token_de_acesso_utilizavel(MARGEM_DO_ACCESS_TOKEN_S)
        if guardado:
            logger.debug("Access token de %s ainda válido; nenhuma requisição gasta.", corredor.id)
            return guardado

    refresh, origem = refresh_token_corrente(corredor, estado)
    if not refresh:
        raise RevokedTokenError(
            f"nenhum refresh token disponível — inscreva o participante: "
            f"python -m src.inscricao link {corredor.id}",
            corredor_id=corredor.id,
        )

    try:
        token = client.refresh_access_token(refresh)
    except RevokedTokenError as erro:
        repo.marcar_reinscricao(corredor.id, str(erro))
        raise RevokedTokenError(
            f"refresh token recusado pelo Strava (origem: {origem}) — "
            f"gere um link novo: python -m src.inscricao link {corredor.id}",
            corredor_id=corredor.id,
        ) from erro

    # Grava ANTES de devolver: se o processo morrer aqui, o token antigo pode já
    # ter sido invalidado pela rotação do Strava e o participante ficaria travado.
    repo.salvar_token(corredor.id, token)
    logger.info("Token de %s renovado (origem do refresh: %s).", corredor.id, origem)
    return token.access_token


def chamar_renovando(
    corredor: Corredor,
    chamada,
    *,
    client: StravaClient,
    repo: CorredorStateRepository,
):
    """Executa `chamada(access_token)`, renovando o token uma vez se der 401.

    Um 401 é rotina (o access token dura 6 horas). Um segundo 401 logo após
    renovar já não é: aí o token foi revogado de verdade, e insistir viraria
    laço infinito.
    """
    try:
        return chamada(obter_access_token(corredor, client=client, repo=repo))
    except ExpiredAccessTokenError:
        logger.info("Access token de %s recusado (401); renovando e repetindo.", corredor.id)

    try:
        return chamada(
            obter_access_token(corredor, client=client, repo=repo, forcar_renovacao=True)
        )
    except ExpiredAccessTokenError as erro:
        repo.marcar_reinscricao(corredor.id, "401 mesmo após renovar o token")
        raise RevokedTokenError(
            f"o Strava recusou o token mesmo após renovar — o participante precisa "
            f"autorizar de novo: python -m src.inscricao link {corredor.id}",
            corredor_id=corredor.id,
        ) from erro


def _primeiro(campos: dict[str, list[str]], nome: str) -> str | None:
    """Primeiro valor de um parâmetro de query, ou None."""
    valores = campos.get(nome) or []
    return valores[0].strip() or None if valores else None
