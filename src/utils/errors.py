"""Exceções da aplicação, numa hierarquia só.

Este módulo **não importa nada do projeto** de propósito: `auth` e `strava_client`
precisam levantar os mesmos erros sem depender um do outro.

O desenho serve a uma exigência concreta do `main.py`: com dezenas de
participantes, a falha de um não pode interromper a coleta dos demais — mas nem
toda falha é de um participante. `QuotaExhaustedError` significa que a cota da
*aplicação* acabou: insistir nos corredores restantes só gera tentativas
condenadas. Por isso ela é irmã, e não filha, dos erros de autorização.

Mensagens de erro são o runbook da pesquisa: quando existe uma ação de
recuperação (gerar um link novo, reinscrever o participante), ela vai **no texto
da exceção**, não num comentário do código.
"""
from __future__ import annotations


class StravaSyncError(RuntimeError):
    """Raiz de tudo que o StravaSync levanta de propósito.

    Herda de `RuntimeError` para não quebrar quem já captura `RuntimeError`
    (o `ConfigError` da Fase 2 nasceu assim).
    """


class StravaError(StravaSyncError):
    """Falha vinda do Strava — rede, resposta inesperada, vazão ou autorização."""


class NetworkError(StravaError):
    """Não foi possível falar com o Strava (timeout, DNS, conexão recusada)."""


class InvalidResponseError(StravaError):
    """O Strava respondeu, mas com algo que não dá para usar (5xx, JSON estranho)."""


class RateLimitError(StravaError):
    """Levamos 429 e as tentativas acabaram — a janela de 15 minutos estourou."""


class QuotaExhaustedError(StravaError):
    """A cota diária da aplicação acabou.

    Não é falha de um corredor: os que faltam ficam **pendentes**, não falharam.
    Quem orquestra deve encerrar a execução, não continuar o laço.
    """


class AuthorizationError(StravaError):
    """Problema no OAuth de um participante específico."""

    def __init__(self, mensagem: str, *, corredor_id: str | None = None) -> None:
        super().__init__(mensagem)
        self.corredor_id = corredor_id

    def __str__(self) -> str:
        mensagem = super().__str__()
        if self.corredor_id:
            return f"corredor {self.corredor_id}: {mensagem}"
        return mensagem


class CodeExchangeError(AuthorizationError):
    """A troca do `code` por token falhou."""


class ExpiredCodeError(CodeExchangeError):
    """O `code` já foi usado ou expirou.

    O `code` é de uso único e de vida curta, e viaja por mensagem até nós — este
    é o erro mais comum da inscrição, e precisa dizer o que fazer.
    """


class InsufficientScopeError(AuthorizationError):
    """O participante aprovou, mas com menos escopo do que a pesquisa precisa.

    A tela do Strava tem caixas de seleção: dá para autorizar desmarcando o
    acesso a atividades privadas. Sem conferir agora, isso só apareceria meses
    depois — e obrigaria a procurar a pessoa de novo.
    """

    def __init__(
        self,
        mensagem: str,
        *,
        concedido: str | None = None,
        exigido: str | None = None,
        corredor_id: str | None = None,
    ) -> None:
        super().__init__(mensagem, corredor_id=corredor_id)
        self.concedido = concedido
        self.exigido = exigido


class UnverifiedScopeError(AuthorizationError):
    """Não deu para saber qual escopo foi concedido.

    O Strava não devolve `scope` na resposta da troca — ele só aparece na URL de
    retorno. Quem colou apenas o `code` não trouxe essa informação.
    """


class ExpiredAccessTokenError(AuthorizationError):
    """O Strava recusou o access token (401).

    Situação **rotineira**: o access token dura 6 horas. Quem chamou deve
    renovar e repetir uma vez. Confundir isto com revogação tiraria o
    participante da coleta por causa de um token simplesmente vencido.
    """


class RevokedTokenError(AuthorizationError):
    """O refresh token não vale mais: o participante precisa autorizar de novo."""


class UnexpectedAthleteError(AuthorizationError):
    """A conta do Strava que autorizou não é a que já estava registrada."""

    def __init__(
        self,
        mensagem: str,
        *,
        esperado: int | None = None,
        recebido: int | None = None,
        corredor_id: str | None = None,
    ) -> None:
        super().__init__(mensagem, corredor_id=corredor_id)
        self.esperado = esperado
        self.recebido = recebido


class StateError(StravaSyncError):
    """Falha ao ler ou gravar o estado local (SQLite)."""
