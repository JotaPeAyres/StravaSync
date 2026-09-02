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

from src.api.strava_client import StravaClient
from src.models.activity import Activity
from src.models.corredor import Corredor
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
        daily_loads = [
            self._activity_service.aggregate_daily(dia, self._activities.por_dia(corredor.id, dia))
            for dia in dias
        ]

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
        """`after=` para a busca: `max(meia-noite UTC de cutover_date,
        ultimo_evento_em - 2 dias)` — a fórmula travada em CLAUDE.md.

        A data-base é `cutover_date`, não `start_date`: o período manual
        (Fase 5.5) já está no SQLite pela adoção, e não deve ser buscado de
        novo no Strava.
        """
        piso = datetime.combine(corredor.cutover_date, time.min, tzinfo=UTC)
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
        """
        corridas = self._activity_service.only_runs(list(payloads))
        atividades: list[Activity] = []
        for payload in corridas:
            try:
                atividades.append(self._activity_service.to_activity(payload))
            except InvalidResponseError as erro:
                logger.warning("Corredor %s: atividade descartada — %s", corredor, erro)
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
