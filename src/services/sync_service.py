"""Orquestra a sincronização ponta a ponta (Strava -> SQLite -> Excel) de **um**
corredor.

Uma instância é compartilhada por todos os corredores da execução — é o que
permite ao `StravaClient`/`RateLimiter` fazerem o mesmo (o limite é da
aplicação inteira, não por atleta). Quem percorre o cadastro e isola a falha
de cada participante é o `main.py`; este módulo propaga as exceções do Strava
sem tratar — decidir se é uma falha do corredor ou algo que interrompe a
execução (`QuotaExhaustedError`) é responsabilidade de quem chama.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import groupby

from src.api.strava_client import StravaClient
from src.models.activity import Activity
from src.models.corredor import Corredor
from src.models.daily_load import DailyLoad
from src.repositories.activity_repository import ActivityRepository
from src.repositories.corredor_state_repository import CorredorStateRepository
from src.repositories.escrita_excel_repository import EscritaExcelRepository
from src.services.activity_service import ActivityService
from src.services.adocao_service import AdocaoService, ResultadoAdocao
from src.services.excel_service import ExcelService
from src.utils import auth
from src.utils.errors import InvalidResponseError
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Folga da busca: o Strava filtra o `after=` por `start_date` em UTC, e uma
# corrida perto da virada do dia local pode chegar numa execução posterior à
# dela — sem essa folga, uma corrida de domingo enviada na terça seria
# perdida para sempre (ver CLAUDE.md, "O after= (armadilha mais séria da fase)").
FOLGA_DA_BUSCA = timedelta(days=2)


@dataclass(frozen=True)
class ResultadoSincronizacao:
    """O que uma sincronização fez por um corredor — insumo de log e relatório."""

    corredor_id: str
    atividades_novas: int
    atividades_atualizadas: int
    atividades_apagadas_no_strava: int
    dias_escritos: int
    adocao: ResultadoAdocao | None


class SyncService:
    """Sincroniza um corredor de cada vez: Strava -> SQLite -> Excel."""

    def __init__(
        self,
        *,
        client: StravaClient,
        activity_service: ActivityService,
        activity_repository: ActivityRepository,
        corredor_state_repository: CorredorStateRepository,
        excel_service: ExcelService,
        escrita_excel_repository: EscritaExcelRepository,
        adocao_service: AdocaoService,
        hoje: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._client = client
        self._activity_service = activity_service
        self._activities = activity_repository
        self._corredor_state = corredor_state_repository
        self._excel = excel_service
        self._escritas = escrita_excel_repository
        self._adocao = adocao_service
        self._hoje = hoje

    def sincronizar(self, corredor: Corredor) -> ResultadoSincronizacao:
        """Sincroniza um único corredor.

        Raises:
            QuotaExhaustedError: a cota diária da aplicação acabou — não é
                falha deste corredor, quem chama deve parar de tentar os
                demais também.
            RevokedTokenError, AuthorizationError, StravaError: falha deste
                corredor especificamente; quem chama isola e segue para o
                próximo (ver `main.py`).
            PlanilhaError, StateError: idem, mas do lado do Excel/SQLite.
        """
        adocao = self._adotar_se_aplicavel(corredor)

        after = self._calcular_after(corredor)
        payloads = auth.chamar_renovando(
            corredor,
            lambda token: self._client.get_activities(token, after=after),
            client=self._client,
            repo=self._corredor_state,
        )

        atividades = self._converter_tolerando_falhas(corredor, payloads)
        gravacao = self._activities.salvar_muitas(corredor.id, atividades)
        apagadas = self._detectar_apagadas(corredor, payloads, after)

        baseline = self._escritas.carregar(corredor.id)
        dias = self._dias_para_escrever(corredor, gravacao.dias_afetados, baseline)
        daily_loads = self._agregar_dias(corredor, dias)

        resultado_excel = self._excel.sincronizar(corredor, daily_loads, ultima_escrita=baseline)
        self._escritas.registrar_muitas(corredor.id, resultado_excel.novas_escritas)

        self._corredor_state.registrar_sincronizacao(
            corredor.id, ultimo_evento_em=self._activities.ultimo_evento_em(corredor.id)
        )

        resultado = ResultadoSincronizacao(
            corredor_id=corredor.id,
            atividades_novas=gravacao.novas,
            atividades_atualizadas=gravacao.atualizadas,
            atividades_apagadas_no_strava=apagadas,
            dias_escritos=resultado_excel.dias_escritos_daily_data,
            adocao=adocao,
        )
        logger.info("Corredor %s: sincronização concluída — %s.", corredor, resultado)
        return resultado

    # --------------------------------------------------------------- interno

    def _adotar_se_aplicavel(self, corredor: Corredor) -> ResultadoAdocao | None:
        """Roda a adoção do período manual, se houver.

        Idempotente (o índice parcial da Fase 5.5 garante que reimportar não
        duplica), então rodar a cada execução é seguro — só redundante depois
        da primeira vez. Decidir uma marca de "já adotado" para pular essa
        redundância fica para quando o custo disso importar de verdade.
        """
        if not corredor.adota_planilha_existente:
            return None
        return self._adocao.adotar(corredor)

    def _calcular_after(self, corredor: Corredor) -> int:
        """`after=` para a busca: `max(meia-noite UTC de cutover_date, ultimo_evento_em
        - 2 dias) - 2 dias de folga` — a fórmula travada em CLAUDE.md, corrigida
        pelo achado do code review da Fase 8.

        A data-base é `cutover_date`, não `start_date`: o período manual
        (Fase 5.5) já está no SQLite pela adoção, e não deve ser buscado de
        novo no Strava.

        O piso também recebe `FOLGA_DA_BUSCA`, e não só o `ultimo_evento_em`:
        `cutover_date` é um dia-calendário **local** do corredor, mas a meia-
        noite UTC dele não coincide com a meia-noite local em fusos != 0. Sem
        a folga, uma corrida perto da virada do dia (fuso positivo) podia
        nunca ser buscada — `after` só avança, então o buraco seria
        permanente. A mesma folga já cobria isso via `ultimo_evento_em -
        FOLGA_DA_BUSCA` a partir da segunda sincronização; faltava na
        primeira, quando `ultimo` ainda é `None`. A contrapartida (a busca
        pode alcançar um pouco do território manual, antes de `cutover_date`)
        é resolvida por `_converter_tolerando_falhas`, que descarta qualquer
        atividade anterior a `start_date` antes de gravar.
        """
        piso = datetime.combine(corredor.cutover_date, time.min, tzinfo=UTC) - FOLGA_DA_BUSCA
        ultimo = self._activities.ultimo_evento_em(corredor.id)
        inicio = max(piso, ultimo - FOLGA_DA_BUSCA) if ultimo is not None else piso
        return int(inicio.timestamp())

    def _converter_tolerando_falhas(
        self, corredor: Corredor, payloads: Sequence[dict]
    ) -> list[Activity]:
        """Filtra e converte, descartando **só** a atividade malformada, não o lote.

        Uma corrida com campo faltando não pode custar as demais do corredor
        — mesmo princípio de isolamento que rege a execução como um todo,
        aplicado um nível abaixo.

        Também descarta o que vier anterior a `start_date` (`dia <= 0`, no
        vocabulário de `Corredor.dia_da_planilha`): o `ExcelService` já
        recusa escrever essas linhas, mas o CLAUDE.md pede uma segunda
        checagem aqui, independente — a folga do `after=` (ver
        `_calcular_after`) pode alcançar alguns dias antes do que o cadastro
        cobre, e nada deve confiar sozinho na outra ponta.
        """
        corridas = self._activity_service.only_runs(list(payloads))
        atividades: list[Activity] = []
        for payload in corridas:
            try:
                atividade = self._activity_service.to_activity(payload)
            except InvalidResponseError as erro:
                logger.warning("Corredor %s: atividade descartada — %s", corredor, erro)
                continue

            if corredor.dia_da_planilha(atividade.day) <= 0:
                logger.warning(
                    "Corredor %s: atividade %s em %s é anterior ao Dia 1 (%s) — fora "
                    "do escopo do estudo, descartada antes de gravar.",
                    corredor,
                    atividade.id,
                    atividade.day.isoformat(),
                    corredor.start_date.isoformat(),
                )
                continue

            atividades.append(atividade)
        return atividades

    def _detectar_apagadas(
        self, corredor: Corredor, payloads: Sequence[dict], after_epoch: int
    ) -> int:
        """Loga (não apaga) atividades que sumiram do Strava. Custa zero requisição.

        Tudo com `start_date_utc >= after` deveria ter voltado nesta mesma
        busca; o que já estava gravado e não voltou foi excluído por lá.
        """
        desde = datetime.fromtimestamp(after_epoch, UTC)
        existentes = self._activities.por_start_date_utc(corredor.id, desde)
        ids_da_api = {payload.get("id") for payload in payloads}

        apagadas = [atividade for atividade in existentes if atividade.id not in ids_da_api]
        for atividade in apagadas:
            logger.warning(
                "Corredor %s: atividade %s (%s, %s) não veio mais do Strava — "
                "presumida apagada; carga mantida no banco e na planilha.",
                corredor,
                atividade.id,
                atividade.day.isoformat(),
                atividade.name or "sem nome",
            )
        return len(apagadas)

    def _agregar_dias(self, corredor: Corredor, dias: list[date]) -> list[DailyLoad]:
        """Agrega cada dia de `dias` num `DailyLoad`, com uma única consulta ao
        banco em vez de uma por dia.

        A janela self-healing de `_dias_para_escrever` pode cobrir meses
        inteiros quando uma execução do scheduler ficou parada — `por_dia`
        em loop viraria uma consulta por dia recuperado. `por_periodo` traz
        tudo de uma vez (a ordenação por `day` já vem do SQL, então
        `groupby` é suficiente para separar por dia).
        """
        if not dias:
            return []

        atividades = self._activities.por_periodo(corredor.id, dias[0], dias[-1])
        por_dia = {dia: tuple(grupo) for dia, grupo in groupby(atividades, key=lambda a: a.day)}
        return [
            self._activity_service.aggregate_daily(dia, por_dia.get(dia, ()))
            for dia in dias
        ]

    def _dias_para_escrever(
        self,
        corredor: Corredor,
        dias_afetados: frozenset[date],
        baseline: dict[date, tuple[float, int]],
    ) -> list[date]:
        """Quais dias mandar para o Excel: o que mudou, mais o que falta preencher.

        A grade é contígua (Fase 5), e por isso não basta reescrever só
        `dias_afetados` — o dia de hoje (ou qualquer dia sem corrida desde a
        última escrita) nunca aparece ali, e ficaria em branco para sempre.
        A baseline gravada pela Fase 5.5 já diz até onde a planilha foi
        escrita da última vez; o resto é preencher, dia a dia, até hoje.
        """
        ultimo_dia_escrito = max(baseline, default=corredor.cutover_date - timedelta(days=1))
        hoje = self._hoje()

        janela: set[date] = set()
        dia = max(ultimo_dia_escrito + timedelta(days=1), corredor.cutover_date)
        while dia <= hoje:
            janela.add(dia)
            dia += timedelta(days=1)

        return sorted(janela | dias_afetados)
