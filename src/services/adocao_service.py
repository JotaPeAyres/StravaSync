"""Adoção de planilhas já preenchidas à mão (Fase 5.5).

Um corredor que já vinha registrando corridas manualmente antes de entrar na
pesquisa não recomeça do zero: o período `[start_date, cutover_date)` é lido
de volta da planilha e importado para o SQLite — sem tocar o Excel, que já
tem esse histórico. Corredor novo (`start_date == cutover_date`) não tem nada
para adotar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.models.corredor import Corredor
from src.repositories.activity_repository import ActivityRepository
from src.services.excel_service import ExcelService
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ResultadoAdocao:
    """Relatório de uma adoção — o que entrou, o que ficou de fora."""

    corredor_id: str
    dias_importados: int
    dias_sem_corrida: int
    # Dias do período manual sem data legível na planilha (linha em branco).
    lacunas: tuple[date, ...]
    # Corrida importada sem saber o tempo (PACE não cobria aquele dia) — a
    # distância entrou, o pace daquele dia fica indeterminado.
    dias_sem_tempo_conhecido: tuple[date, ...]
    primeiro_dia_sob_gestao_do_app: date

    @property
    def tem_pendencias(self) -> bool:
        return bool(self.lacunas or self.dias_sem_tempo_conhecido)


class AdocaoService:
    """Lê o histórico manual da planilha e o importa para o SQLite."""

    def __init__(
        self, excel_service: ExcelService, activity_repository: ActivityRepository
    ) -> None:
        self._excel = excel_service
        self._activities = activity_repository

    def adotar(self, corredor: Corredor) -> ResultadoAdocao:
        """Roda a adoção de um único corredor.

        Idempotente: rodar de novo (planilha sem mudanças) reimporta o mesmo
        período e atualiza as mesmas linhas, sem duplicar — ver o índice
        parcial usado por `ActivityRepository.importar_historico`.
        """
        if not corredor.adota_planilha_existente:
            logger.debug("Corredor %s: sem período manual (start == cutover).", corredor)
            return ResultadoAdocao(
                corredor_id=corredor.id,
                dias_importados=0,
                dias_sem_corrida=0,
                lacunas=(),
                dias_sem_tempo_conhecido=(),
                primeiro_dia_sob_gestao_do_app=corredor.cutover_date,
            )

        historico = self._excel.ler_historico_manual(corredor)

        sem_tempo = tuple(
            registro.day
            for registro in historico.registros
            if registro.tem_corrida and registro.tempo_total_s is None
        )
        sem_corrida = sum(1 for registro in historico.registros if not registro.tem_corrida)

        gravacao = self._activities.importar_historico(corredor.id, historico.registros)

        resultado = ResultadoAdocao(
            corredor_id=corredor.id,
            dias_importados=gravacao.total,
            dias_sem_corrida=sem_corrida,
            lacunas=historico.lacunas,
            dias_sem_tempo_conhecido=sem_tempo,
            primeiro_dia_sob_gestao_do_app=corredor.cutover_date,
        )

        if historico.lacunas:
            logger.warning(
                "Corredor %s: %d dia(s) sem data legível no período manual — %s.",
                corredor,
                len(historico.lacunas),
                ", ".join(dia.isoformat() for dia in historico.lacunas),
            )
        logger.info(
            "Corredor %s: adoção concluída — %d dia(s) importado(s), %d de descanso, "
            "%d lacuna(s), %d sem tempo conhecido. Gestão do app a partir de %s.",
            corredor,
            resultado.dias_importados,
            resultado.dias_sem_corrida,
            len(resultado.lacunas),
            len(resultado.dias_sem_tempo_conhecido),
            resultado.primeiro_dia_sob_gestao_do_app.isoformat(),
        )
        return resultado
