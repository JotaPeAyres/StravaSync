"""Controle de vazão das chamadas ao Strava.

O limite do Strava é **por aplicação**, não por atleta: 100 requisições a cada 15
minutos e 1000 por dia, divididas por todos os participantes da pesquisa. A ~2
requisições por corredor, 100 participantes gastam ~200 requisições numa
execução — cabe no dia, mas estoura a janela de 15 minutos. Por isso isto é
requisito, não otimização.

Uma instância por execução, compartilhada pelo `StravaClient` único. Um cliente
por corredor derrotaria o mecanismo inteiro. É mono-thread — não há lock.

**Nada disto persiste entre execuções, e é de propósito**: toda resposta do
Strava traz o uso corrente nos cabeçalhos, então a primeira resposta já
reposiciona o limitador. Um contador local seria uma segunda fonte de verdade,
capaz de divergir da real.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from src.utils.errors import QuotaExhaustedError
from src.utils.logger import get_logger

logger = get_logger(__name__)

LIMITE_CURTO_PADRAO = 100
LIMITE_DIARIO_PADRAO = 1000

JANELA_CURTA_S = 900  # 15 minutos
MAX_TENTATIVAS_429 = 3

# Teto de bom senso para qualquer espera: a janela curta inteira mais folga.
ESPERA_MAXIMA_S = 960


class RateLimiter:
    """Espaça as chamadas e respeita os tetos anunciados pelo Strava."""

    def __init__(
        self,
        *,
        pausa_s: float = 1.0,
        reserva: int = 10,
        dormir: Callable[[float], None] = time.sleep,
        relogio: Callable[[], float] = time.monotonic,
        agora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._pausa_s = max(0.0, pausa_s)
        self._reserva = max(0, reserva)
        self._dormir = dormir
        self._relogio = relogio
        self._agora = agora

        self._ultima_chamada: float | None = None
        self._limite_curto = LIMITE_CURTO_PADRAO
        self._limite_diario = LIMITE_DIARIO_PADRAO
        self._uso_curto = 0
        self._uso_diario = 0

    def antes_da_chamada(self) -> None:
        """Bloqueia até que seja seguro fazer a próxima requisição.

        Raises:
            QuotaExhaustedError: se a cota diária da aplicação acabou.
        """
        self._verificar_cota_diaria()
        self._esperar_janela_curta()
        self._respeitar_pausa()
        self._ultima_chamada = self._relogio()

    def registrar_resposta(self, headers: Mapping[str, str]) -> None:
        """Atualiza o estado a partir dos cabeçalhos de vazão da resposta.

        Cabeçalho ausente, vazio ou malformado **nunca** levanta: um cabeçalho
        quebrado do lado do Strava não pode derrubar a coleta da pesquisa. O
        estado anterior é mantido e o problema fica registrado em DEBUG.
        """
        limite = _par(headers.get("X-RateLimit-Limit"))
        if limite is not None:
            self._limite_curto, self._limite_diario = limite

        uso = _par(headers.get("X-RateLimit-Usage"))
        if uso is not None:
            self._uso_curto, self._uso_diario = uso
        else:
            # Sem cabeçalho não dá para saber o uso real; contamos localmente
            # para não avançar às cegas até a próxima resposta informativa.
            self._uso_curto += 1
            self._uso_diario += 1
            logger.debug("Resposta sem X-RateLimit-Usage; uso estimado localmente.")

    def esperar_apos_429(self, tentativa: int, headers: Mapping[str, str]) -> float:
        """Dorme depois de um 429 e devolve quantos segundos esperou.

        O Strava geralmente **não** manda `Retry-After`; quando não manda, a
        espera vai até a próxima fronteira de quarto de hora, que é quando a
        janela curta zera.
        """
        espera = _retry_after(headers)
        if espera is None:
            espera = self._segundos_ate_a_proxima_janela()
        elif espera < 60:
            # Backoff só faz sentido em espera curta: se já vamos aguardar a
            # virada da janela, multiplicar por tentativa seria dormir à toa.
            espera *= max(1, tentativa)

        espera = min(espera, ESPERA_MAXIMA_S)

        logger.warning(
            "429 do Strava (tentativa %d/%d) — aguardando %.0fs. %s",
            tentativa,
            MAX_TENTATIVAS_429,
            espera,
            self.resumo,
        )
        # Não mexemos em `_ultima_chamada`: ele marca a última *requisição*, e o
        # relógio monotônico já andou o tempo da espera — a pausa seguinte sai
        # satisfeita sozinha, sem dormir de novo à toa.
        self._dormir(espera)
        # Como em `_esperar_janela_curta`: depois de esperar, o contador local
        # zera. Sem isso, `_uso_curto` continuaria no teto e a chamada seguinte
        # (via `antes_da_chamada`) dormiria quase uma janela inteira de novo,
        # dobrando a recuperação depois de cada 429 — a próxima resposta real
        # corrige o valor de qualquer forma, via `registrar_resposta`.
        self._uso_curto = 0
        return espera

    @property
    def resumo(self) -> str:
        """Uma linha para o log: quanto da cota da aplicação já foi gasta."""
        return (
            f"uso {self._uso_curto}/{self._limite_curto} (15min) · "
            f"{self._uso_diario}/{self._limite_diario} (dia)"
        )

    def _verificar_cota_diaria(self) -> None:
        """A cota diária zera à meia-noite UTC — esperar horas não é opção.

        Num job agendado, dormir até o dia virar seria pior do que parar: a
        execução seguinte retoma de onde esta parou.
        """
        if self._uso_diario >= self._limite_diario:
            raise QuotaExhaustedError(
                f"cota diária da aplicação esgotada ({self.resumo}); "
                "os corredores restantes ficam para a próxima execução"
            )

    def _esperar_janela_curta(self) -> None:
        """A janela de 15 minutos zera em fronteiras fixas — vale esperar."""
        if self._uso_curto < self._limite_curto - self._reserva:
            return

        espera = min(self._segundos_ate_a_proxima_janela(), ESPERA_MAXIMA_S)
        logger.info(
            "Janela de 15 min quase no teto (%s, reserva=%d) — aguardando %.0fs.",
            self.resumo,
            self._reserva,
            espera,
        )
        self._dormir(espera)
        # A janela virou: o contador local zera e a próxima resposta confirma.
        self._uso_curto = 0

    def _respeitar_pausa(self) -> None:
        """Espaça as chamadas usando relógio monotônico (imune a ajuste de horário)."""
        if self._pausa_s <= 0 or self._ultima_chamada is None:
            return
        restante = self._pausa_s - (self._relogio() - self._ultima_chamada)
        if restante > 0:
            self._dormir(restante)

    def _segundos_ate_a_proxima_janela(self) -> float:
        """Segundos até a próxima fronteira de quarto de hora (00, 15, 30, 45)."""
        agora = self._agora()
        decorridos = (agora.minute % 15) * 60 + agora.second + agora.microsecond / 1_000_000
        return max(1.0, JANELA_CURTA_S - decorridos)


def _par(valor: str | None) -> tuple[int, int] | None:
    """Lê um cabeçalho no formato `"curto,diario"`; devolve None se não der."""
    if not valor:
        return None
    partes = valor.split(",")
    if len(partes) < 2:
        logger.debug("Cabeçalho de vazão em formato inesperado: %r", valor)
        return None
    try:
        return int(partes[0].strip()), int(partes[1].strip())
    except ValueError:
        logger.debug("Cabeçalho de vazão com valor não numérico: %r", valor)
        return None


def _retry_after(headers: Mapping[str, str]) -> float | None:
    """Lê `Retry-After` em segundos, quando presente e numérico."""
    bruto = headers.get("Retry-After")
    if not bruto:
        return None
    try:
        return max(1.0, float(bruto.strip()))
    except ValueError:
        # O formato em data HTTP é raro aqui e não vale o parser; a fronteira de
        # quarto de hora é uma espera segura de qualquer jeito.
        logger.debug("Retry-After não numérico: %r", bruto)
        return None
