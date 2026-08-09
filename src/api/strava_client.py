"""Cliente da API do Strava: OAuth2, renovação de token e busca de atividades.

Implementação na Fase 3.
"""
from __future__ import annotations


class StravaClient:
    """Encapsula as chamadas HTTP à API do Strava."""

    def refresh_access_token(self) -> str:
        """Renova e retorna o Access Token. (Stub — Fase 3.)"""
        raise NotImplementedError  # TODO(Fase 3)

    def get_activities(self) -> list[dict]:
        """Busca as atividades do atleta, tratando paginação. (Stub — Fase 3.)"""
        raise NotImplementedError  # TODO(Fase 3)
