# StravaSync

Aplicação Python **single-athlete** que sincroniza automaticamente as corridas do Strava para uma planilha Excel de **monitoramento de carga de treino (ACWR / EWMA)**.

A cada execução a aplicação consulta a API do Strava, identifica novas corridas, **agrega os dados por dia** e alimenta a planilha de carga do corredor — preservando as fórmulas e os gráficos do modelo. Um banco SQLite local guarda os dados ricos de cada atividade (para evitar duplicidades e permitir estatísticas futuras).

> **Status:** fase inicial — o repositório contém apenas a documentação de escopo/arquitetura. O código ainda não foi implementado. Veja o backlog em [TASKS.md](TASKS.md).

---

## O que a aplicação faz

- Autentica no Strava via **OAuth2** e renova o Access Token automaticamente.
- Busca as atividades do atleta e importa **apenas corridas (Run)**, sem duplicar.
- **Agrega as corridas por dia** e escreve na planilha de carga: `Data`, `Carga diária (Km)`, `Pace` e `Tempo total`.
- **Preserva** as fórmulas (EWMA agudo 7d, EWMA crônico 28d, ACWR, PCAC, etc.), constantes e named ranges do template — nunca sobrescreve colunas calculadas.
- Mantém um **SQLite** local com os dados detalhados por atividade (FC, cadência, elevação, ID, tipo…).
- Executa diariamente via **scheduler**, com **logs** de cada etapa.

## A planilha (uma por corredor)

O arquivo base `Cópia de Planilha_carga_corrida.xlsx` é o **template**. O app é **single-athlete**: uma instância = um corredor = uma planilha.

**Onboarding de um novo corredor (manual):** copie o template para um novo arquivo, aponte a configuração (`EXCEL_PATH`) para ele e defina a **data de início** (`START_DATE`, que corresponde ao `Dia 1`). A partir daí o app preenche os dias de forma contígua (dias sem corrida recebem carga `0`).

Abas do modelo:

| Aba | Conteúdo | O app escreve |
|-----|----------|---------------|
| `Daily_Data` | 1 linha por dia; carga diária + indicadores ACWR/EWMA | `Data`, `Carga diária (Km)` |
| `Weekly_Desacoplado` | Agregação semanal (100% fórmulas) | — (nada) |
| `Gráficos` | Gráficos de acompanhamento (named ranges dinâmicos) | — (nada) |
| `PACE` | 1 linha por dia; ritmo e tempo | `Data`, `Carga diária (Km)`, `PACE`, `Tempo total de treinamento` |

Detalhes das colunas (entrada vs. fórmula) em [PROJECT_SCOPE.md](PROJECT_SCOPE.md).

## Tecnologias

Python 3.13+, `requests`/`httpx`, `openpyxl`, `pandas`, SQLite, `python-dotenv`, `logging`, `pytest`.

## Configuração

Variáveis de ambiente (arquivo `.env`):

```
STRAVA_CLIENT_ID=...
STRAVA_CLIENT_SECRET=...
STRAVA_REFRESH_TOKEN=...
EXCEL_PATH=./data/corredor.xlsx      # cópia do template para este corredor
TEMPLATE_PATH=./data/template.xlsx   # modelo base (Cópia de Planilha_carga_corrida.xlsx)
START_DATE=2026-01-01                # data do "Dia 1" da planilha
```

## Como rodar

```bash
uv sync
python -m src.main
```

## Documentação

- [PROJECT_SCOPE.md](PROJECT_SCOPE.md) — objetivos, dados armazenados e modelo do Excel.
- [ARCHITECTURE.md](ARCHITECTURE.md) — arquitetura em camadas e responsabilidades.
- [TASKS.md](TASKS.md) — backlog por fases.
