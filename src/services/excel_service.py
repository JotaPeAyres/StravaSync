"""Atualiza a planilha de carga (ACWR) preservando fórmulas e named ranges.

Escreve apenas as colunas de entrada:
- Daily_Data: B (Data), C (Carga diária Km)
- PACE: B (Data), C (Carga Km), D (PACE), E (Tempo total)

Mapeia data -> linha a partir da data de início (Dia 1):
    dia = (data - data_inicio).days + 1  ->  linha = dia + 1

Implementação na Fase 5.
"""
from __future__ import annotations


class ExcelService:
    """Escreve o agregado diário nas abas Daily_Data e PACE."""

    def update(self, daily_loads: list) -> None:
        """Grava os DailyLoad no Excel, sem tocar em colunas de fórmula. (Stub — Fase 5.)"""
        raise NotImplementedError  # TODO(Fase 5)
