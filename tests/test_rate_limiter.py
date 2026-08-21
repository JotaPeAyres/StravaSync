"""Testes do controle de vazão (Fase 3).

O limite do Strava é da aplicação inteira, dividido por 50+ participantes — o
que este módulo erra sai caro para todo mundo ao mesmo tempo.

Nenhum teste dorme de verdade: `dormir`, `relogio` e `agora` são injetados.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.api.rate_limiter import (
    ESPERA_MAXIMA_S,
    JANELA_CURTA_S,
    LIMITE_CURTO_PADRAO,
    RateLimiter,
)
from src.utils.errors import QuotaExhaustedError


class _Relogio:
    """Relógio monotônico falso, que só anda quando alguém dorme."""

    def __init__(self) -> None:
        self.agora = 1000.0
        self.dormidas: list[float] = []

    def dormir(self, segundos: float) -> None:
        self.dormidas.append(segundos)
        self.agora += segundos

    def avancar(self, segundos: float) -> None:
        self.agora += segundos

    def __call__(self) -> float:
        return self.agora


def _limitador(relogio: _Relogio, *, pausa_s: float = 1.0, reserva: int = 10, **kwargs):
    return RateLimiter(
        pausa_s=pausa_s,
        reserva=reserva,
        dormir=relogio.dormir,
        relogio=relogio,
        **kwargs,
    )


def _cabecalhos(uso_curto: int, uso_diario: int, **extra: str) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": "100,1000",
        "X-RateLimit-Usage": f"{uso_curto},{uso_diario}",
        **extra,
    }


def test_primeira_chamada_nao_espera():
    relogio = _Relogio()
    _limitador(relogio).antes_da_chamada()

    assert relogio.dormidas == []


def test_pausa_entre_chamadas_e_respeitada():
    """É o botão principal de vazão: espaçar as chamadas ao longo da janela."""
    relogio = _Relogio()
    limitador = _limitador(relogio, pausa_s=2.0)

    limitador.antes_da_chamada()
    limitador.antes_da_chamada()

    assert relogio.dormidas == [2.0]


def test_pausa_desconta_o_tempo_ja_gasto():
    """Se a requisição em si demorou, não faz sentido dormir a pausa inteira."""
    relogio = _Relogio()
    limitador = _limitador(relogio, pausa_s=2.0)

    limitador.antes_da_chamada()
    relogio.avancar(1.5)
    limitador.antes_da_chamada()

    assert relogio.dormidas == [pytest.approx(0.5)]


def test_pausa_zero_nao_dorme():
    relogio = _Relogio()
    limitador = _limitador(relogio, pausa_s=0)

    limitador.antes_da_chamada()
    limitador.antes_da_chamada()

    assert relogio.dormidas == []


def test_le_uso_dos_cabecalhos():
    relogio = _Relogio()
    limitador = _limitador(relogio)

    limitador.registrar_resposta(_cabecalhos(42, 210))

    assert "42/100" in limitador.resumo
    assert "210/1000" in limitador.resumo


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-RateLimit-Limit": ""},
        {"X-RateLimit-Usage": "abc,def"},
        {"X-RateLimit-Usage": "100"},
        {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "sei lá"},
    ],
)
def test_cabecalho_quebrado_nao_derruba_a_coleta(headers):
    """Um cabeçalho estranho do Strava não pode interromper a pesquisa."""
    relogio = _Relogio()
    limitador = _limitador(relogio)

    limitador.registrar_resposta(headers)

    assert limitador.resumo  # segue utilizável


def test_janela_curta_no_teto_espera_ate_a_virada():
    """A janela de 15 min zera em fronteira fixa — vale esperar por ela."""
    relogio = _Relogio()
    agora = datetime(2026, 8, 18, 12, 5, tzinfo=UTC)  # 5 min dentro da janela
    limitador = _limitador(relogio, agora=lambda: agora)

    limitador.registrar_resposta(_cabecalhos(LIMITE_CURTO_PADRAO - 10, 200))
    limitador.antes_da_chamada()

    assert relogio.dormidas
    assert relogio.dormidas[0] == pytest.approx(JANELA_CURTA_S - 5 * 60)


def test_reserva_deixa_folga_para_uma_inscricao_manual():
    """Com reserva=10, paramos em 90/100 e sobram 10 requisições."""
    relogio = _Relogio()
    limitador = _limitador(relogio, reserva=10)

    limitador.registrar_resposta(_cabecalhos(89, 200))
    limitador.antes_da_chamada()
    assert relogio.dormidas == []  # ainda não bateu o teto reservado

    limitador.registrar_resposta(_cabecalhos(90, 201))
    limitador.antes_da_chamada()
    assert relogio.dormidas  # agora esperou


def test_cota_diaria_esgotada_para_a_execucao():
    """Dormir até a meia-noite UTC num job agendado é inaceitável."""
    relogio = _Relogio()
    limitador = _limitador(relogio)

    limitador.registrar_resposta(_cabecalhos(10, 1000))

    with pytest.raises(QuotaExhaustedError, match="cota diária"):
        limitador.antes_da_chamada()
    assert relogio.dormidas == []


def test_429_honra_retry_after():
    relogio = _Relogio()
    limitador = _limitador(relogio)

    espera = limitador.esperar_apos_429(1, {"Retry-After": "30"})

    assert espera == 30
    assert relogio.dormidas == [30]


def test_429_sem_retry_after_espera_a_virada_da_janela():
    """O Strava geralmente não manda Retry-After."""
    relogio = _Relogio()
    agora = datetime(2026, 8, 18, 12, 10, tzinfo=UTC)  # 10 min dentro da janela
    limitador = _limitador(relogio, agora=lambda: agora)

    espera = limitador.esperar_apos_429(1, {})

    assert espera == pytest.approx(JANELA_CURTA_S - 10 * 60)


def test_429_com_retry_after_curto_faz_backoff():
    relogio = _Relogio()
    limitador = _limitador(relogio)

    assert limitador.esperar_apos_429(1, {"Retry-After": "5"}) == 5
    assert limitador.esperar_apos_429(2, {"Retry-After": "5"}) == 10
    assert limitador.esperar_apos_429(3, {"Retry-After": "5"}) == 15


def test_espera_tem_teto():
    """Nenhuma espera pode prender o processo por tempo indeterminado."""
    relogio = _Relogio()
    limitador = _limitador(relogio)

    assert limitador.esperar_apos_429(1, {"Retry-After": "999999"}) == ESPERA_MAXIMA_S


def test_espera_do_429_ja_satisfaz_a_pausa():
    """Depois de aguardar 30s por um 429, dormir mais 1s de pausa é desperdício."""
    relogio = _Relogio()
    limitador = _limitador(relogio, pausa_s=1.0)

    limitador.esperar_apos_429(1, {"Retry-After": "30"})
    limitador.antes_da_chamada()

    assert relogio.dormidas == [30]


def test_resumo_funciona_antes_de_qualquer_resposta():
    relogio = _Relogio()

    assert "0/100" in _limitador(relogio).resumo
