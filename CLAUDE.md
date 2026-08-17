# CLAUDE.md

Contexto do projeto para o Claude Code. **Leia isto antes de agir.** Documento vivo — atualize conforme o projeto avança.

## O que é

**StravaSync**: app Python **single-athlete** que sincroniza corridas do Strava para uma planilha Excel de **carga de treino (ACWR / EWMA)**. Agrega as corridas **por dia**, alimenta a planilha do corredor **preservando fórmulas/gráficos**, e mantém um SQLite com os dados ricos por atividade (dedupe + estatísticas futuras).

## Estado atual

- **Fase 1 (Estrutura): concluída e já na `main`** (a branch `fase-1-estrutura` está mergeada; pode ser apagada).
- **Fase 2 (Configuração): concluída** na branch **`fase-2-configuracao`** — `Config` tipado, logging central, modelos finalizados, testes. Ainda não mergeada.
- **Próxima: Fase 3 (Strava/OAuth)** — ver `TASKS.md`.

## Convenções de trabalho (IMPORTANTE)

- **Uma branch por fase**: `fase-N-nome` (ex.: `fase-2-configuracao`), criada a partir da `main`.
- **Docs de planejamento estão no `.gitignore`** (locais, não versionados): `PROJECT_SCOPE.md`, `ARCHITECTURE.md`, `TASKS.md`. Eles **existem no disco** — leia-os para escopo, arquitetura e backlog detalhado.
- Gerenciador de pacotes: **uv**. Mensagens de commit em português, terminando com `Co-Authored-By: Claude ...`.
- **Não fazer push nem merge sem o usuário pedir.**

## Modelo do Excel (o ponto mais importante e não-óbvio)

Template: `Cópia de Planilha_carga_corrida.xlsx` (copiado por corredor). O app escreve **apenas as células de entrada**; **nunca** toca colunas de fórmula nem named ranges.

- **`Daily_Data`** (1 linha/dia, linhas 2–366): escreve **B (Data)** e **C (Carga diária Km)**. Colunas D–J são fórmulas (EWMA agudo 7d, EWMA crônico 28d, ACWR, maior corrida 30d, razão sessão-específica, limites 0.8/1.3).
- **`PACE`** (linhas 2–154): escreve **B (Data)**, **C (Carga Km)**, **D (PACE)**, **E (Tempo total)**.
- **`Weekly_Desacoplado`** (linhas 2–53, 52 semanas) e **`Gráficos`**: só fórmulas / named ranges — **não escrever**.
- **Dias contíguos**: `Dia 1` = `START_DATE`; preencher todos os dias; **dia sem corrida → Carga 0** (em `PACE`, Pace/Tempo em branco). Mapa: `dia = (data - START_DATE).days + 1` → `linha = dia + 1`.
- **`START_DATE` = data de entrada do corredor na pesquisa** = o primeiro dia em que o projeto passa a buscar dados dele. É **dado do estudo**, registrado explicitamente por corredor — nunca inferido de quando o script rodou pela primeira vez (uma execução atrasada excluiria dias de coleta em silêncio). Consequências:
  - O que o corredor correu **antes** dessa data está fora do escopo. A Fase 3 deve pedir ao Strava só o que interessa (`after=START_DATE` em `/athlete/activities`), e a Fase 6 deve descartar o que sobrar de mais antigo — `dia <= 0` cairia na linha 1 (cabeçalho) ou acima.
  - A grade também tem teto: `Daily_Data` vai até a linha 366 (365 dias) e `PACE` até a 154 (153 dias). Passado isso, a planilha do corredor acabou — decidir o que fazer (nova cópia? erro?) é assunto da Fase 5.
  - **Depois da planilha gravada, a célula `B2` é a fonte da verdade**, não o `.env`. O app lê `B2` e, se o `.env` divergir, **aborta em vez de escrever** — editar a data no `.env` de uma planilha já preenchida deslocaria a grade inteira. O `.env` só faz o bootstrap da primeira execução.
  - Pode estar no **futuro**: o corredor entra na pesquisa na semana que vem, a config é feita hoje. Enquanto `hoje < START_DATE` não há nada para sincronizar.
- **Cada corredor da pesquisa = uma instância**: um `.env` (com a sua `START_DATE`) + uma planilha. Corredores entram em datas diferentes, então as `START_DATE` são diferentes entre si.
- **Unidades (Strava → Excel)**: distância m → km; pace = tempo_total_dia ÷ km_dia (min/km); tempo total = Σ `moving_time`.

## Decisões travadas

- **Single-athlete**: 1 conjunto de credenciais, 1 planilha. Onboarding de novo corredor = copiar o template + definir `START_DATE` (passo manual), sendo `START_DATE` a data em que a coleta daquele corredor começa.
- **Planilhas preenchidas à mão são adotadas, não recomeçadas** (ver Fase 5.5 no `TASKS.md`). Duas datas governam isso:
  - `START_DATE` = o `Dia 1` (célula `B2`); `CUTOVER_DATE` = o primeiro dia em que o app escreve. Corredor novo: as duas coincidem.
  - **Antes do corte o app nunca escreve** — só lê, para conhecer o histórico. Depois do corte, ele assume.
  - **Problema que isso cria**: o dedupe é por `activity_id`, mas linha preenchida à mão **não tem ID** — o SQLite não sabe que o dia já foi contabilizado, e a mesma corrida existe na planilha e na API. Daí o corte: ele define, por data, de quem é cada linha.
  - **Edição manual continua possível depois do corte** (esteira, treino sem relógio), mas é exceção. O app guarda o valor que escreveu em cada dia; se encontrar valor diferente, trata como edição humana — **preserva e loga**, não sobrescreve.
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
  utils/config.py ✅, logger.py ✅, auth.py (Fase 3)
  main.py (executável), scheduler.py   # Fase 7
tests/            # pytest
data/             # .gitkeep; *.db, *.xlsx e *.log de runtime são ignorados
```

## Configuração e logging (Fase 2 — já implementados)

- **`utils/config.py`**: `load_config()` → `Config` congelado. Obrigatórias: as 3 credenciais do Strava + `START_DATE`. Opcionais com padrão: `CUTOVER_DATE` (padrão = `START_DATE`), `EXCEL_PATH`, `TEMPLATE_PATH`, `LOG_LEVEL`, `LOG_FILE`. `config.adota_planilha_existente` diz se há histórico manual antes do corte.
  - Erros são **acumulados** e levantados de uma vez em `ConfigError` — nada de corrigir o `.env` uma variável por vez.
  - **Caminhos relativos partem de `PROJECT_ROOT`**, não do CWD (o scheduler da Fase 7 pode rodar de qualquer lugar).
  - O ambiente tem precedência sobre o `.env` (permite sobrescrever em CI/agendador).
  - Segredos ficam fora do `repr`; use `config.safe_summary()` para logar a config.
- **`utils/logger.py`**: `setup_logging(level, log_file, force=False)` é **idempotente**; chamada só no `main.py`. Demais módulos usam `get_logger(__name__)`. Arquivo rotativo em UTF-8 (acentos); `httpx`/`httpcore`/`urllib3` silenciados em WARNING.
- **`Activity.date` é horário LOCAL e ingênuo** (o `start_date_local` do Strava). A planilha é indexada pelo dia local: corrida às 22h de 01/01 vai para a linha de 01/01, não a de 02/01 em UTC. A Fase 3 deve ler `start_date_local`, **não** `start_date`.
- Conversões de unidade moram nos modelos: `Activity.distance_km`, `Activity.day`, `DailyLoad.pace_min_km`, `DailyLoad.tempo_total` (timedelta), `DailyLoad.is_rest_day`, `DailyLoad.dia_de_descanso()`.

## Ambiente e comandos

- Python **3.12.7**; `.venv` já criado (gitignored). `uv` **0.12.2** no PATH.
- `uv sync` — instala deps · `uv run pytest` · `uv run ruff check .` · `uv run python -m src.main`.
- Config via `.env` (veja `.env.example`). O `.env` local já existe, com as credenciais **em branco** — preencher na Fase 3.
- **Ruff**: o `pyproject.toml` não fixa `select`, então valem os padrões da versão instalada (0.16.1), que incluem `DTZ` e `C4`. Considerar fixar um `select` explícito na etapa de Qualidade.
- Nota: a variável `SSL_CERT_FILE` quebrada (sobra de outro projeto) ainda aparece como *warning* do `uv` no shell do Claude — inofensiva, não bloqueia nada.

## Próximo passo — Fase 3 (Strava: OAuth + API)

Em nova branch `fase-3-strava` a partir da `main` (mergear a Fase 2 antes):

- `utils/auth.py`: trocar o `refresh_token` por um access token, com renovação automática.
- `api/strava_client.py`: cliente `httpx` — buscar atleta e atividades, paginação, tratamento de erros.
- Filtrar apenas `type == "Run"`; mapear o JSON para `Activity` (atenção ao `start_date_local`).
- Passar `after=` (epoch) na busca de atividades, para não paginar o histórico inteiro do atleta. **A data é `CUTOVER_DATE`, não `START_DATE`** — numa planilha adotada, o trecho entre as duas já foi preenchido à mão e o app não vai reescrevê-lo. Para corredor novo as duas coincidem.
- Referência detalhada: `TASKS.md` › Fase 3.
