# CLAUDE.md

Contexto do projeto para o Claude Code. **Leia isto antes de agir.** Documento vivo — atualize conforme o projeto avança.

## O que é

**StravaSync**: app Python **single-athlete** que sincroniza corridas do Strava para uma planilha Excel de **carga de treino (ACWR / EWMA)**. Agrega as corridas **por dia**, alimenta a planilha do corredor **preservando fórmulas/gráficos**, e mantém um SQLite com os dados ricos por atividade (dedupe + estatísticas futuras).

## Estado atual

- **Fase 1 (Estrutura): concluída** — scaffolding na branch **`fase-1-estrutura`** (2 commits à frente da `main`; ainda não mergeada).
- `main` contém só a documentação + o template Excel. O código (`src/`, `tests/`, `pyproject.toml`) está na `fase-1-estrutura`.
- **Próxima: Fase 2 (Configuração)** — ver `TASKS.md`.

## Convenções de trabalho (IMPORTANTE)

- **Uma branch por fase**: `fase-N-nome` (ex.: `fase-2-configuracao`), criada a partir da `main`.
- **Docs de planejamento estão no `.gitignore`** (locais, não versionados): `PROJECT_SCOPE.md`, `ARCHITECTURE.md`, `TASKS.md`. Eles **existem no disco** — leia-os para escopo, arquitetura e backlog detalhado.
- Gerenciador de pacotes: **uv**. Mensagens de commit em português, terminando com `Co-Authored-By: Claude ...`.
- **Não fazer push nem merge sem o usuário pedir.**

## Modelo do Excel (o ponto mais importante e não-óbvio)

Template: `Cópia de Planilha_carga_corrida.xlsx` (copiado por corredor). O app escreve **apenas as células de entrada**; **nunca** toca colunas de fórmula nem named ranges.

- **`Daily_Data`** (1 linha/dia, linhas 2–366): escreve **B (Data)** e **C (Carga diária Km)**. Colunas D–J são fórmulas (EWMA agudo 7d, EWMA crônico 28d, ACWR, maior corrida 30d, razão sessão-específica, limites 0.8/1.3).
- **`PACE`** (linhas 2–154): escreve **B (Data)**, **C (Carga Km)**, **D (PACE)**, **E (Tempo total)**.
- **`Weekly_Desacoplado`** e **`Gráficos`**: só fórmulas / named ranges — **não escrever**.
- **Dias contíguos**: `Dia 1` = `START_DATE`; preencher todos os dias; **dia sem corrida → Carga 0** (em `PACE`, Pace/Tempo em branco). Mapa: `dia = (data - START_DATE).days + 1` → `linha = dia + 1`.
- **Unidades (Strava → Excel)**: distância m → km; pace = tempo_total_dia ÷ km_dia (min/km); tempo total = Σ `moving_time`.

## Decisões travadas

- **Single-athlete**: 1 conjunto de credenciais, 1 planilha. Onboarding de novo corredor = copiar o template + definir `START_DATE` (passo manual).
- **Excel só carga/ACWR/PACE**; dados ricos (FC, cadência, elevação, Activity ID, tipo, velocidade) ficam **só no SQLite**.
- Modelos: `Activity` (por atividade, SQLite) e `DailyLoad` (agregado por dia, Excel).
- Lib Excel: **openpyxl** (ok hoje, pois `Gráficos` está vazia). ⚠️ Se adicionarem gráficos ao template, o openpyxl os apaga ao reescrever — reavaliar então.

## Estrutura do código (stubs marcados com `TODO(Fase N)`)

```
src/
  api/strava_client.py                 # Fase 3
  services/activity_service.py         # Fases 3/6
  services/sync_service.py             # Fase 6 (orquestra tudo)
  services/excel_service.py            # Fase 5 (escreve entradas, preserva fórmulas)
  services/database_service.py         # Fase 4
  models/activity.py, models/daily_load.py
  repositories/activity_repository.py  # Fase 4
  utils/config.py (Fase 2), logger.py (Fase 2), auth.py (Fase 3)
  main.py (executável), scheduler.py   # Fase 7
tests/            # pytest
data/             # .gitkeep; *.db e *.xlsx de runtime são ignorados
```

## Ambiente e comandos

- Python **3.12.7**; `.venv` já criado (gitignored). `uv` **0.12.2** no PATH.
- `uv sync` — instala deps · `uv run pytest` · `uv run ruff check .` · `python -m src.main`.
- Config via `.env` (veja `.env.example`): `STRAVA_CLIENT_ID/SECRET/REFRESH_TOKEN`, `EXCEL_PATH`, `TEMPLATE_PATH`, `START_DATE`.
- Nota: a variável `SSL_CERT_FILE` quebrada (sobra de outro projeto) foi removida — `uv` funciona sem workaround. O Git Bash interno do Claude roda com PATH restrito; no PowerShell/terminal do usuário o `uv` funciona normalmente.

## Próximo passo — Fase 2 (Configuração)

Em nova branch `fase-2-configuracao` a partir da `main` (mergear a Fase 1 na `main` antes, se quiser um trunk limpo):

- `utils/config.py`: carregar `.env` via `python-dotenv` → objeto `Config` tipado (credenciais, caminhos, `START_DATE` como `date`).
- `utils/logger.py`: logging estruturado central.
- Finalizar/ajustar os modelos (`Activity`, `DailyLoad`) conforme necessário.
- Referência detalhada: `TASKS.md` › Fase 2.
