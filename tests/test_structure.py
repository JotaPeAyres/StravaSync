"""Testes de fumaça da estrutura (Fase 1): os módulos das camadas são importáveis."""
from __future__ import annotations

import importlib

import pytest

MODULOS = (
    "src.main",
    "src.scheduler",
    "src.api.strava_client",
    "src.services.activity_service",
    "src.services.sync_service",
    "src.services.excel_service",
    "src.services.database_service",
    "src.repositories.activity_repository",
    "src.utils.auth",
    "src.utils.config",
    "src.utils.logger",
)


@pytest.mark.parametrize("modulo", MODULOS)
def test_modulo_importavel(modulo):
    assert importlib.import_module(modulo) is not None


def test_main_importavel():
    import src.main

    assert callable(src.main.main)
