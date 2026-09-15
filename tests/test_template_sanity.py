"""Sinaliza, sem ambiguidade, a ausência do template real do Excel (Fase 8).

Três arquivos de teste (Excel, adoção, sincronização) usam `skipif` quando o
template não está presente — correto para não travar a suíte, mas isso também
significa que a ausência só reduz a contagem de "passed", sem nenhum
"failed", o que passa despercebido num ambiente novo. Este teste, sem
`skipif`, torna essa ausência impossível de não notar.
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "Cópia de Planilha_carga_corrida.xlsx"


def test_template_do_excel_esta_presente_no_repositorio():
    assert TEMPLATE.is_file(), (
        f"{TEMPLATE} não encontrado — sem ele, os testes de Excel/Sync/Adoção "
        "são pulados em silêncio (skipif), não falham"
    )
