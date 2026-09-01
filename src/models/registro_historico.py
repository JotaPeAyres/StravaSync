"""Um dia do período manual da planilha — antes do `cutover_date` (Fase 5.5).

Distinto de `DailyLoad`: aquele é o que o **app** vai escrever; este é o que
já estava na planilha quando o corredor foi adotado, lido de volta.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RegistroHistorico:
    """O que já estava gravado num dia do período preenchido à mão.

    `tempo_total_s` é `None` quando a aba `PACE` não cobre esse dia (grid
    menor que `Daily_Data`, ou célula em branco) — o registro ainda é
    importado, só sem como calcular o pace daquele dia.
    """

    day: date
    carga_km: float
    tempo_total_s: int | None = None

    @property
    def tem_corrida(self) -> bool:
        """Uma corrida de fato, e não um dia de descanso (`Carga 0`)."""
        return self.carga_km > 0
