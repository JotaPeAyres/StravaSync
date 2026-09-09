"""Testes do wrapper de agendamento (Fase 7).

`scheduler.py` não tem regra de negócio própria — só delega para `main.main()`
e garante que nenhuma exceção escape para quem o chamou (Windows Task
Scheduler, cron). O que se testa aqui é só essa rede de segurança.
"""
from __future__ import annotations

import pytest

from src import scheduler as modulo_scheduler


def test_repassa_o_codigo_de_saida_do_main(monkeypatch):
    monkeypatch.setattr(modulo_scheduler, "sincronizar", lambda: 0)

    assert modulo_scheduler.executar() == 0


def test_repassa_falha_do_main(monkeypatch):
    monkeypatch.setattr(modulo_scheduler, "sincronizar", lambda: 1)

    assert modulo_scheduler.executar() == 1


def test_excecao_nao_tratada_vira_codigo_1_e_log_critical(monkeypatch, caplog):
    def explodir():
        raise RuntimeError("banco inacessível")

    monkeypatch.setattr(modulo_scheduler, "sincronizar", explodir)

    assert modulo_scheduler.executar() == 1
    assert "banco inacessível" in caplog.text


def test_excecao_nao_tratada_nao_escapa_de_executar(monkeypatch):
    def explodir():
        raise RuntimeError("bug qualquer")

    monkeypatch.setattr(modulo_scheduler, "sincronizar", explodir)

    try:
        modulo_scheduler.executar()
    except RuntimeError:  # pragma: no cover
        pytest.fail("a exceção vazou de executar()")
