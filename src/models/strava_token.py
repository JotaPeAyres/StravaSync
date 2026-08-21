"""Resposta do endpoint de token do Strava.

O `scope` **não vem** nesta resposta: o Strava devolve apenas
`{token_type, expires_at, expires_in, refresh_token, access_token, athlete}`.
O escopo concedido aparece só na query da URL de retorno da autorização — daí o
campo ser preenchido de fora, por quem leu essa URL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.utils.errors import InvalidResponseError


@dataclass(frozen=True)
class StravaToken:
    """Um par de tokens do Strava, com o instante de expiração do access token."""

    # Segredos: fora do repr para não vazarem em log ou traceback.
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expira_em: datetime  # sempre aware, em UTC

    # Só vem na troca do `code`; a renovação não devolve nenhum dos dois.
    athlete_id: int | None = None
    scope: str | None = None

    @property
    def expirado(self) -> bool:
        return datetime.now(UTC) >= self.expira_em

    def valido_por(self, margem_s: float = 300) -> bool:
        """True se o token ainda vale daqui a `margem_s` segundos.

        A margem existe porque entre decidir usar o token e a requisição chegar
        ao Strava passa tempo — um token que expira em 3 segundos é inútil.
        """
        return (self.expira_em - datetime.now(UTC)).total_seconds() > margem_s

    @classmethod
    def from_payload(cls, payload: dict, *, scope: str | None = None) -> StravaToken:
        """Constrói o token a partir do JSON do Strava.

        Args:
            payload: corpo da resposta de `/oauth/token`.
            scope: escopo lido da URL de retorno. Se o payload trouxer `scope`
                (hoje não traz), ele tem precedência — assim, se o Strava mudar,
                passamos a usar a fonte mais confiável sem alterar o código.

        Raises:
            InvalidResponseError: se faltar campo obrigatório ou vier malformado.
        """
        access_token = str(payload.get("access_token") or "")
        refresh_token = str(payload.get("refresh_token") or "")
        expires_at = payload.get("expires_at")

        faltando = [
            nome
            for nome, valor in (
                ("access_token", access_token),
                ("refresh_token", refresh_token),
                ("expires_at", expires_at),
            )
            if not valor
        ]
        if faltando:
            raise InvalidResponseError(
                f"resposta de token sem {', '.join(faltando)} — o Strava mudou o contrato?"
            )

        try:
            expira_em = datetime.fromtimestamp(float(expires_at), UTC)
        except (TypeError, ValueError, OSError, OverflowError) as erro:
            raise InvalidResponseError(
                f"expires_at={expires_at!r} não é um instante válido"
            ) from erro

        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            expira_em=expira_em,
            athlete_id=_id_do_atleta(payload),
            scope=_texto_ou_none(payload.get("scope")) or scope,
        )


def _id_do_atleta(payload: dict) -> int | None:
    """Lê `athlete.id` do payload da troca, tolerando ausência e lixo."""
    atleta = payload.get("athlete")
    if not isinstance(atleta, dict):
        return None
    try:
        return int(atleta["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _texto_ou_none(valor: object) -> str | None:
    texto = str(valor or "").strip()
    return texto or None
