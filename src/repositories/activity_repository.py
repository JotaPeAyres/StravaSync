"""Persistência de atividades no SQLite e checagem de duplicidade.

Nunca conhece a API do Strava. Implementação na Fase 4.
"""
from __future__ import annotations


class ActivityRepository:
    """Salva e consulta atividades no banco local."""

    def exists(self, activity_id: int) -> bool:
        """Retorna True se o Activity ID já foi importado. (Stub — Fase 4.)"""
        raise NotImplementedError  # TODO(Fase 4)

    def save(self, activity) -> None:
        """Insere uma atividade. (Stub — Fase 4.)"""
        raise NotImplementedError  # TODO(Fase 4)
