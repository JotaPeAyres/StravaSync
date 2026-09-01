"""Testes do ExcelService (Fase 5).

O que este módulo erra vira planilha corrompida ou grade deslocada — e a
planilha é o produto final da pesquisa. Por isso a insistência em três pontos:
fórmulas nunca são tocadas, `B2` gravado é intocável, e uma falha de um
corredor nunca pode deixar o arquivo dele pela metade.
"""
from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pytest
from openpyxl.cell.cell import Cell

from src.models.corredor import Corredor
from src.models.daily_load import DailyLoad
from src.services.excel_service import (
    ABA_DAILY_DATA,
    ABA_PACE,
    ExcelService,
    ResultadoExcel,
)
from src.utils.errors import (
    DataBaseDivergenteError,
    PlanilhaEmUsoError,
    PlanilhaNaoEncontradaError,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "Cópia de Planilha_carga_corrida.xlsx"

INICIO = date(2026, 1, 1)


pytestmark = pytest.mark.skipif(
    not TEMPLATE.is_file(), reason="template real não encontrado no repositório"
)


@pytest.fixture
def planilha(tmp_path) -> Path:
    """Cópia isolada do template, como se fosse o resultado do onboarding."""
    destino = tmp_path / "p001.xlsx"
    shutil.copy(TEMPLATE, destino)
    return destino


def _corredor(excel_path: Path, *, start_date: date = INICIO) -> Corredor:
    return Corredor(
        id="p001",
        nome="Ana",
        refresh_token="token",
        start_date=start_date,
        cutover_date=start_date,
        excel_path=excel_path,
    )


def _carga(dias_apos_inicio: int, *, km: float = 10.0, minutos: float = 60.0) -> DailyLoad:
    dia = INICIO + timedelta(days=dias_apos_inicio)
    return DailyLoad(day=dia, carga_km=km, tempo_total_s=int(minutos * 60))


def _descanso(dias_apos_inicio: int) -> DailyLoad:
    return DailyLoad.dia_de_descanso(INICIO + timedelta(days=dias_apos_inicio))


def _abrir(caminho: Path):
    return openpyxl.load_workbook(caminho)


def _valor_data(celula: Cell) -> date | None:
    """Uma célula com formato de data volta do openpyxl como `datetime`, nunca `date`."""
    valor = celula.value
    return valor.date() if valor is not None else None


# --------------------------------------------------------------- caminho feliz


def test_grava_data_e_carga_em_daily_data(planilha):
    ExcelService().sincronizar(_corredor(planilha), [_carga(0, km=12.5)])

    wb = _abrir(planilha)
    aba = wb[ABA_DAILY_DATA]
    assert _valor_data(aba["B2"]) == INICIO
    assert aba["C2"].value == 12.5


def test_grava_data_carga_pace_e_tempo_em_pace(planilha):
    ExcelService().sincronizar(_corredor(planilha), [_carga(0, km=10.0, minutos=50.0)])

    wb = _abrir(planilha)
    aba = wb[ABA_PACE]
    assert _valor_data(aba["B2"]) == INICIO
    assert aba["C2"].value == 10.0
    assert aba["D2"].value == pytest.approx(5.0)  # 50min / 10km
    assert aba["E2"].value == timedelta(minutes=50)


def test_dia_1_e_a_linha_2_terceiro_dia_e_a_linha_4(planilha):
    ExcelService().sincronizar(_corredor(planilha), [_carga(0), _carga(1), _carga(2)])

    wb = _abrir(planilha)
    aba = wb[ABA_DAILY_DATA]
    assert _valor_data(aba["B2"]) == INICIO
    assert _valor_data(aba["B3"]) == INICIO + timedelta(days=1)
    assert _valor_data(aba["B4"]) == INICIO + timedelta(days=2)


def test_dia_de_descanso_carga_zero_pace_e_tempo_em_branco(planilha):
    ExcelService().sincronizar(_corredor(planilha), [_descanso(0)])

    wb = _abrir(planilha)
    assert wb[ABA_DAILY_DATA]["C2"].value == 0.0
    aba_pace = wb[ABA_PACE]
    assert aba_pace["C2"].value == 0.0
    assert aba_pace["D2"].value is None
    assert aba_pace["E2"].value is None


def test_devolve_contagem_de_dias_escritos(planilha):
    resultado = ExcelService().sincronizar(
        _corredor(planilha), [_carga(0), _carga(1), _descanso(2)]
    )

    assert resultado == ResultadoExcel(
        dias_escritos_daily_data=3, dias_escritos_pace=3, dias_descartados=0
    )


def test_lista_vazia_nao_toca_o_arquivo(planilha):
    mtime_antes = planilha.stat().st_mtime_ns

    resultado = ExcelService().sincronizar(_corredor(planilha), [])

    assert resultado == ResultadoExcel(0, 0)
    assert planilha.stat().st_mtime_ns == mtime_antes


# --------------------------------------------------------------- fórmulas/formatação


def test_preserva_formulas_das_colunas_calculadas(planilha):
    formula_original = _abrir(planilha)[ABA_DAILY_DATA]["D2"].value

    ExcelService().sincronizar(_corredor(planilha), [_carga(0)])

    formula_depois = _abrir(planilha)[ABA_DAILY_DATA]["D2"].value
    assert formula_depois == formula_original
    assert formula_depois.startswith("=IF(ROW()-1<7")


def test_preserva_named_ranges(planilha):
    antes = _abrir(planilha).defined_names.keys()

    ExcelService().sincronizar(_corredor(planilha), [_carga(0)])

    depois = _abrir(planilha).defined_names.keys()
    assert set(depois) == set(antes)


def test_nao_escreve_fora_das_colunas_de_entrada(planilha):
    """F/G/H (ACWR, maior corrida, razão) são fórmula — nunca literais."""
    ExcelService().sincronizar(_corredor(planilha), [_carga(0)])

    aba = _abrir(planilha)[ABA_DAILY_DATA]
    assert str(aba["F2"].value).startswith("=")
    assert str(aba["G2"].value).startswith("=")
    assert str(aba["H2"].value).startswith("=")


# --------------------------------------------------------------- limites da grade


def test_dia_anterior_ao_inicio_e_descartado(planilha):
    resultado = ExcelService().sincronizar(_corredor(planilha), [_carga(-1)])

    assert resultado == ResultadoExcel(0, 0, dias_descartados=1)
    wb = _abrir(planilha)
    assert wb[ABA_DAILY_DATA]["B2"].value is None


def test_dia_alem_do_teto_de_pace_ainda_escreve_em_daily_data(planilha):
    # dia 154 (índice 153) é a primeira linha que estoura o teto de PACE (154).
    resultado = ExcelService().sincronizar(_corredor(planilha), [_carga(153)])

    assert resultado.dias_escritos_daily_data == 1
    assert resultado.dias_escritos_pace == 0
    assert resultado.dias_alem_do_limite_pace == 1

    wb = _abrir(planilha)
    assert _valor_data(wb[ABA_DAILY_DATA].cell(row=155, column=2)) == INICIO + timedelta(days=153)


def test_dia_alem_do_teto_de_daily_data_e_descartado_nas_duas_abas(planilha):
    # dia 366 (índice 365) estoura o teto de Daily_Data (366) e o de PACE (154).
    resultado = ExcelService().sincronizar(_corredor(planilha), [_carga(365)])

    assert resultado.dias_escritos_daily_data == 0
    assert resultado.dias_escritos_pace == 0
    assert resultado.dias_alem_do_limite_daily_data == 1
    assert resultado.dias_alem_do_limite_pace == 1


# --------------------------------------------------------------- B2 como fonte da verdade


def test_bootstrap_com_planilha_vazia_nao_levanta(planilha):
    assert _abrir(planilha)[ABA_DAILY_DATA]["B2"].value is None
    ExcelService().sincronizar(_corredor(planilha), [_carga(0)])  # não deve levantar


def test_b2_divergente_aborta_sem_escrever(planilha):
    ExcelService().sincronizar(_corredor(planilha), [_carga(0)])  # grava B2 = INICIO

    outro_cadastro = _corredor(planilha, start_date=INICIO + timedelta(days=1))

    with pytest.raises(DataBaseDivergenteError):
        ExcelService().sincronizar(outro_cadastro, [_carga(0)])


def test_b2_igual_ao_cadastro_nao_levanta(planilha):
    ExcelService().sincronizar(_corredor(planilha), [_carga(0)])
    ExcelService().sincronizar(_corredor(planilha), [_carga(1)])  # não deve levantar

    wb = _abrir(planilha)
    assert _valor_data(wb[ABA_DAILY_DATA]["B3"]) == INICIO + timedelta(days=1)


def test_b2_ilegivel_aborta(planilha):
    wb = _abrir(planilha)
    wb[ABA_DAILY_DATA]["B2"] = "não é uma data"
    wb.save(planilha)

    with pytest.raises(DataBaseDivergenteError):
        ExcelService().sincronizar(_corredor(planilha), [_carga(0)])


# --------------------------------------------------------------- erros de arquivo


def test_planilha_inexistente_levanta_erro_claro(tmp_path):
    corredor = _corredor(tmp_path / "nao-existe.xlsx")

    with pytest.raises(PlanilhaNaoEncontradaError, match="p001"):
        ExcelService().sincronizar(corredor, [_carga(0)])


def test_planilha_aberta_no_replace_levanta_erro_especifico(planilha, monkeypatch):
    import src.services.excel_service as modulo

    def falhar(*_args, **_kwargs):
        raise PermissionError("arquivo em uso")

    monkeypatch.setattr(modulo.os, "replace", falhar)

    with pytest.raises(PlanilhaEmUsoError):
        ExcelService().sincronizar(_corredor(planilha), [_carga(0)])

    # O original não pode ter sido corrompido pela tentativa.
    wb = _abrir(planilha)
    assert wb[ABA_DAILY_DATA]["B2"].value is None
    # E o temporário não pode ter sobrado no diretório do corredor.
    sobras = list(planilha.parent.glob(".*.tmp.xlsx"))
    assert sobras == []
