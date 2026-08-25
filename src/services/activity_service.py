"""Regras de negócio das atividades: filtrar corridas, validar e agregar por dia.

A agregação por dia é da Fase 6; a Fase 3 entrega o filtro e a conversão do JSON
do Strava para o modelo interno.

⚠️ **O horário que decide a linha da planilha é o `start_date_local`.** A grade é
indexada pelo dia local do corredor: uma corrida às 22h de 01/01 pertence à linha
de 01/01, e não à de 02/01, que é o dia em UTC. E o `start_date_local` termina
com `Z` — mas esse `Z` é mentira: o valor já está no fuso do atleta. Convertê-lo
para UTC deslocaria a corrida de dia, ou seja, de **linha da planilha**.

O `start_date` (UTC de verdade) também é lido, mas para outra pergunta: é a marca
d'água do `after=`, que o Strava filtra por UTC. Os dois campos convivem no
modelo justamente porque respondem a coisas diferentes — ver `_data_local` e
`_data_utc`.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from src.models.activity import Activity
from src.utils.errors import InvalidResponseError
from src.utils.logger import get_logger

logger = get_logger(__name__)

# O requisito da pesquisa é corrida a pé. `TrailRun` passa (o `type` dela é
# "Run"); `VirtualRun` — esteira, Zwift — não. Se aparecer muita esteira no log,
# a decisão de incluí-la é da pesquisa, e fica para a Fase 6.
TIPO_DE_CORRIDA = "Run"


class ActivityService:
    """Converte e agrega atividades da API no modelo interno."""

    def only_runs(self, activities: list[dict]) -> list[dict]:
        """Filtra apenas atividades do tipo Run.

        Os tipos descartados vão para o log em DEBUG: é assim que se descobre
        quanta esteira (`VirtualRun`) a pesquisa está deixando de fora.
        """
        corridas = [a for a in activities if a.get("type") == TIPO_DE_CORRIDA]

        descartadas = Counter(
            str(a.get("type") or "?") for a in activities if a.get("type") != TIPO_DE_CORRIDA
        )
        if descartadas:
            logger.debug(
                "Descartadas %d atividades que não são %s: %s",
                sum(descartadas.values()),
                TIPO_DE_CORRIDA,
                ", ".join(f"{tipo}={n}" for tipo, n in sorted(descartadas.items())),
            )

        return corridas

    def to_activity(self, payload: dict) -> Activity:
        """Converte o JSON de uma atividade no modelo `Activity`.

        Raises:
            InvalidResponseError: se faltar campo obrigatório ou vier malformado.
        """
        rotulo = str(payload.get("name") or payload.get("id") or "atividade sem identificação")

        return Activity(
            id=_inteiro(payload, "id", rotulo),
            name=str(payload.get("name") or ""),
            date=_data_local(payload, rotulo),
            distance_m=_numero(payload, "distance", rotulo),
            moving_time_s=_inteiro(payload, "moving_time", rotulo),
            # `elapsed_time` ausente cai no tempo em movimento: é o melhor
            # palpite e não afeta a carga, que usa o `moving_time`.
            elapsed_time_s=(
                _inteiro(payload, "elapsed_time", rotulo)
                if payload.get("elapsed_time") is not None
                else _inteiro(payload, "moving_time", rotulo)
            ),
            type=str(payload.get("type") or TIPO_DE_CORRIDA),
            average_speed=_opcional(payload, "average_speed"),
            average_heartrate=_opcional(payload, "average_heartrate"),
            max_heartrate=_opcional(payload, "max_heartrate"),
            elevation_gain=_opcional(payload, "total_elevation_gain"),
            # `calories` não existe na listagem `/athlete/activities` — só no
            # endpoint de atividade individual, que custaria uma requisição por
            # corrida. Com 50+ participantes isso incendiaria a cota.
            calories=None,
            cadence=_opcional(payload, "average_cadence"),
            start_date_utc=_data_utc(payload, rotulo),
        )

    def to_activities(self, payloads: list[dict]) -> list[Activity]:
        """Converte uma lista de atividades, na ordem recebida."""
        return [self.to_activity(payload) for payload in payloads]

    def aggregate_daily(self, activities: list) -> list:
        """Agrega as corridas por dia (DailyLoad). (Stub — Fase 6.)"""
        raise NotImplementedError  # TODO(Fase 6)


def _data_local(payload: dict, rotulo: str) -> datetime:
    """Lê `start_date_local` como horário local **ingênuo**.

    O Strava manda `"2026-01-01T22:15:30Z"`, mas o valor já está no fuso do
    atleta: o `Z` é decorativo. Descartamos o fuso em vez de converter — uma
    conversão jogaria a corrida das 22h para o dia seguinte, ou seja, para a
    linha errada da planilha.
    """
    bruto = payload.get("start_date_local")
    if not bruto:
        raise InvalidResponseError(
            f"{rotulo}: sem `start_date_local` — sem ele não há como saber a que dia "
            "da planilha a corrida pertence"
        )

    try:
        momento = datetime.fromisoformat(str(bruto))
    except ValueError as erro:
        raise InvalidResponseError(f"{rotulo}: start_date_local={bruto!r} inválido") from erro

    return momento.replace(tzinfo=None)


def _data_utc(payload: dict, rotulo: str) -> datetime | None:
    """Lê `start_date` — o instante ABSOLUTO da largada, em UTC.

    Ao contrário do `start_date_local`, aqui o fuso é real e é convertido, não
    descartado. Este é o valor que alimenta a marca d'água do `after=`, que o
    Strava filtra por UTC.

    Ausência ou valor inválido **não** é fatal, e a assimetria com o
    `_data_local` é proposital: sem `start_date_local` não há como saber a que
    linha da planilha a corrida pertence, enquanto sem `start_date` a busca
    seguinte apenas recomeça do `cutover_date` e repagina histórico — custa
    cota, não corrompe dado. Derrubar a coleta do participante por isso seria
    desproporcional.
    """
    bruto = payload.get("start_date")
    if not bruto:
        logger.debug("%s: sem `start_date`; a marca d'água do after= não avança por ela.", rotulo)
        return None

    try:
        momento = datetime.fromisoformat(str(bruto))
    except ValueError:
        logger.debug("%s: start_date=%r inválido; ignorado.", rotulo, bruto)
        return None

    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(UTC)


def _inteiro(payload: dict, campo: str, rotulo: str) -> int:
    """Campo numérico obrigatório, lido como inteiro."""
    return int(_numero(payload, campo, rotulo))


def _numero(payload: dict, campo: str, rotulo: str) -> float:
    valor = payload.get(campo)
    if valor is None:
        raise InvalidResponseError(f"{rotulo}: sem `{campo}`")
    try:
        return float(valor)
    except (TypeError, ValueError) as erro:
        raise InvalidResponseError(f"{rotulo}: {campo}={valor!r} não é numérico") from erro


def _opcional(payload: dict, campo: str) -> float | None:
    """Campo que pode faltar (sem cinta de FC, sem barômetro) — ausência não é erro."""
    valor = payload.get(campo)
    if valor is None:
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        logger.debug("Campo opcional %s com valor inesperado: %r", campo, valor)
        return None
