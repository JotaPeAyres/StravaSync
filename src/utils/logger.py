"""Configuração central de logging. Implementação completa na Fase 2."""
from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    """Retorna um logger nomeado. (Configuração completa na Fase 2.)"""
    return logging.getLogger(name)
