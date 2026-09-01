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
    COL_CARGA,
    COL_DATA,
    COL_TEMPO,
    ExcelService,
    ResultadoExcel,
)
from src.utils.errors import (
    DataBaseDivergenteError,
    GradeDesalinhadaError,
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


def _corredor(
    excel_path: Path, *, start_date: date = INICIO, cutover_date: date | None = None
) -> Corredor:
    return Corredor(
        id="p001",
        nome="Ana",
        refresh_token="token",
        start_date=start_date,
        cutover_date=cutover_date or start_date,
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

    assert resultado.dias_escritos_daily_data == 3
    assert resultado.dias_escritos_pace == 3
    assert resultado.dias_descartados == 0
    assert set(resultado.novas_escritas) == {
        INICIO,
        INICIO + timedelta(days=1),
        INICIO + timedelta(days=2),
    }


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


# --------------------------------------------------------------- Fase 5.5: corte manual


def _preencher_linha(
    wb, corredor: Corredor, dia: date, carga_km: float, minutos: float | None = None
) -> None:
    """Escreve uma linha diretamente, como se um humano tivesse digitado."""
    linha = corredor.dia_da_planilha(dia) + 1
    wb[ABA_DAILY_DATA].cell(row=linha, column=COL_DATA).value = dia
    wb[ABA_DAILY_DATA].cell(row=linha, column=COL_CARGA).value = carga_km
    if minutos is not None:
        wb[ABA_PACE].cell(row=linha, column=COL_DATA).value = dia
        wb[ABA_PACE].cell(row=linha, column=COL_TEMPO).value = timedelta(minutes=minutos)


def test_dia_antes_do_cutover_nao_e_sobrescrito(planilha):
    cutover = INICIO + timedelta(days=5)
    corredor = _corredor(planilha, cutover_date=cutover)

    wb = _abrir(planilha)
    _preencher_linha(wb, corredor, INICIO, 7.5)
    wb.save(planilha)

    resultado = ExcelService().sincronizar(corredor, [_carga(0, km=999.0)])

    assert resultado.dias_sob_gestao_manual == 1
    assert resultado.dias_escritos_daily_data == 0

    wb = _abrir(planilha)
    assert wb[ABA_DAILY_DATA]["C2"].value == 7.5


def test_dia_do_cutover_e_escrito_normalmente(planilha):
    cutover = INICIO + timedelta(days=2)
    corredor = _corredor(planilha, cutover_date=cutover)

    resultado = ExcelService().sincronizar(corredor, [_carga(2, km=12.0)])

    assert resultado.dias_escritos_daily_data == 1
    assert resultado.dias_sob_gestao_manual == 0


# --------------------------------------------------------------- Fase 5.5: edição humana


def test_edicao_humana_pos_corte_e_preservada(planilha):
    corredor = _corredor(planilha)
    primeiro = ExcelService().sincronizar(corredor, [_carga(0, km=10.0, minutos=50.0)])

    # Humano corrige a distância na planilha, com o relógio na mão.
    wb = _abrir(planilha)
    wb[ABA_DAILY_DATA].cell(row=2, column=COL_CARGA).value = 99.0
    wb.save(planilha)

    segundo = ExcelService().sincronizar(
        corredor, [_carga(0, km=11.0, minutos=55.0)], ultima_escrita=primeiro.novas_escritas
    )

    assert segundo.dias_preservados == 1
    assert segundo.dias_escritos_daily_data == 0
    assert INICIO not in segundo.novas_escritas

    wb = _abrir(planilha)
    assert wb[ABA_DAILY_DATA].cell(row=2, column=COL_CARGA).value == 99.0


def test_sem_edicao_humana_app_sobrescreve_normalmente(planilha):
    """Uma nova sincronização legítima (ex.: Strava corrigiu a distância) não é edição humana."""
    corredor = _corredor(planilha)
    primeiro = ExcelService().sincronizar(corredor, [_carga(0, km=10.0, minutos=50.0)])

    segundo = ExcelService().sincronizar(
        corredor, [_carga(0, km=10.5, minutos=52.0)], ultima_escrita=primeiro.novas_escritas
    )

    assert segundo.dias_preservados == 0
    assert segundo.dias_escritos_daily_data == 1
    wb = _abrir(planilha)
    assert wb[ABA_DAILY_DATA]["C2"].value == 10.5


def test_dia_de_descanso_nao_e_falso_positivo_de_edicao(planilha):
    """Baseline de descanso (tempo=0) não pode parecer editado por causa da célula em branco."""
    corredor = _corredor(planilha)
    primeiro = ExcelService().sincronizar(corredor, [_descanso(0)])

    segundo = ExcelService().sincronizar(
        corredor, [_descanso(0)], ultima_escrita=primeiro.novas_escritas
    )

    assert segundo.dias_preservados == 0


def test_sem_baseline_sempre_escreve(planilha):
    """`ultima_escrita=None` é o comportamento da Fase 5: nunca detecta edição."""
    corredor = _corredor(planilha)
    ExcelService().sincronizar(corredor, [_carga(0, km=10.0)])

    wb = _abrir(planilha)
    wb[ABA_DAILY_DATA].cell(row=2, column=COL_CARGA).value = 42.0
    wb.save(planilha)

    resultado = ExcelService().sincronizar(corredor, [_carga(0, km=15.0)])

    assert resultado.dias_preservados == 0
    assert resultado.dias_escritos_daily_data == 1
    wb = _abrir(planilha)
    assert wb[ABA_DAILY_DATA]["C2"].value == 15.0


# --------------------------------------------------------------- Fase 5.5: ler_historico_manual


def test_historico_vazio_quando_corredor_novo(tmp_path):
    """start_date == cutover_date: sem período manual, nem precisa abrir o arquivo."""
    corredor = _corredor(tmp_path / "nao-existe.xlsx")

    historico = ExcelService().ler_historico_manual(corredor)

    assert historico.registros == ()
    assert historico.lacunas == ()


def test_le_periodo_manual_completo(planilha):
    cutover = INICIO + timedelta(days=3)
    corredor = _corredor(planilha, cutover_date=cutover)

    wb = _abrir(planilha)
    _preencher_linha(wb, corredor, INICIO, 10.0, 50.0)
    _preencher_linha(wb, corredor, INICIO + timedelta(days=1), 0.0)
    _preencher_linha(wb, corredor, INICIO + timedelta(days=2), 8.0, 45.0)
    wb.save(planilha)

    historico = ExcelService().ler_historico_manual(corredor)

    assert [r.day for r in historico.registros] == [
        INICIO,
        INICIO + timedelta(days=1),
        INICIO + timedelta(days=2),
    ]
    assert historico.registros[0].carga_km == 10.0
    assert historico.registros[0].tempo_total_s == 3000
    assert historico.registros[1].tem_corrida is False
    assert historico.lacunas == ()


def test_lacuna_quando_linha_sem_data(planilha):
    cutover = INICIO + timedelta(days=3)
    corredor = _corredor(planilha, cutover_date=cutover)

    wb = _abrir(planilha)
    _preencher_linha(wb, corredor, INICIO, 10.0)
    # INICIO + 1 dia fica em branco de propósito — a pessoa não logou aquele dia.
    _preencher_linha(wb, corredor, INICIO + timedelta(days=2), 8.0)
    wb.save(planilha)

    historico = ExcelService().ler_historico_manual(corredor)

    assert historico.lacunas == (INICIO + timedelta(days=1),)
    assert [r.day for r in historico.registros] == [INICIO, INICIO + timedelta(days=2)]


def test_grade_desalinhada_levanta(planilha):
    cutover = INICIO + timedelta(days=3)
    corredor = _corredor(planilha, cutover_date=cutover)

    wb = _abrir(planilha)
    _preencher_linha(wb, corredor, INICIO, 10.0)  # B2 correto — não é isso que falha
    # Linha do Dia 2 recebe a data do Dia 6 por engano — como se uma linha
    # tivesse sido inserida ou apagada à mão na planilha.
    wb[ABA_DAILY_DATA]["B3"] = INICIO + timedelta(days=5)
    wb[ABA_DAILY_DATA]["C3"] = 10.0
    wb.save(planilha)

    with pytest.raises(GradeDesalinhadaError):
        ExcelService().ler_historico_manual(corredor)


def test_tempo_none_quando_pace_nao_cobre_o_dia(planilha):
    # Teto de PACE (154) é bem menor que o de Daily_Data (366).
    dia_alvo = INICIO + timedelta(days=155)
    cutover = INICIO + timedelta(days=160)
    corredor = _corredor(planilha, cutover_date=cutover)

    wb = _abrir(planilha)
    _preencher_linha(wb, corredor, dia_alvo, 10.0)  # sem tempo: além do teto de PACE
    wb.save(planilha)

    historico = ExcelService().ler_historico_manual(corredor)

    alvo = next(r for r in historico.registros if r.day == dia_alvo)
    assert alvo.carga_km == 10.0
    assert alvo.tempo_total_s is None


def test_leitura_para_no_teto_da_grade(planilha):
    cutover = INICIO + timedelta(days=400)  # além dos 365 dias de Daily_Data

    historico = ExcelService().ler_historico_manual(_corredor(planilha, cutover_date=cutover))

    assert historico.registros == ()
    assert len(historico.lacunas) == 365  # grade inteira, nada preenchido


def test_carga_nao_numerica_vira_zero_com_warning(planilha, caplog):
    cutover = INICIO + timedelta(days=1)
    corredor = _corredor(planilha, cutover_date=cutover)

    wb = _abrir(planilha)
    wb[ABA_DAILY_DATA]["B2"] = INICIO
    wb[ABA_DAILY_DATA]["C2"] = "descanso"
    wb.save(planilha)

    with caplog.at_level("WARNING"):
        historico = ExcelService().ler_historico_manual(corredor)

    assert historico.registros[0].carga_km == 0.0
    assert "não é numérico" in caplog.text


def test_ler_historico_valida_b2(planilha):
    wb = _abrir(planilha)
    wb[ABA_DAILY_DATA]["B2"] = INICIO + timedelta(days=1)  # diverge do cadastro
    wb.save(planilha)

    cutover = INICIO + timedelta(days=2)
    corredor = _corredor(planilha, cutover_date=cutover)

    with pytest.raises(DataBaseDivergenteError):
        ExcelService().ler_historico_manual(corredor)
