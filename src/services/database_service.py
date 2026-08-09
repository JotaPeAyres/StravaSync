"""Infraestrutura do SQLite: conexão, criação de schema e migrações simples.

Implementação na Fase 4.
"""
from __future__ import annotations


class DatabaseService:
    """Gerencia a conexão e o schema do banco local."""

    def init_schema(self) -> None:
        """Cria a tabela `activities` se não existir. (Stub — Fase 4.)"""
        raise NotImplementedError  # TODO(Fase 4)
