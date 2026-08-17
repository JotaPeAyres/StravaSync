# CLAUDE.md

Contexto do projeto para o Claude Code. **Leia isto antes de agir.** Documento vivo — atualize conforme o projeto avança.

## O que é

**StravaSync**: app Python que sincroniza corridas do Strava para planilhas Excel de **carga de treino (ACWR / EWMA)**, no contexto de uma **pesquisa com múltiplos corredores**. A cada execução percorre **todos os participantes cadastrados**: agrega as corridas de cada um **por dia**, alimenta a planilha dele **preservando fórmulas/gráficos**, e mantém um SQLite com os dados ricos por atividade (dedupe + estatísticas futuras).

## Estado atual

- **Fase 1 (Estrutura): concluída e na `main`** (branch `fase-1-estrutura` mergeada; pode ser apagada).
- **Fase 2 (Configuração): concluída e na `main`** — config em duas camadas (`.env` global + `corredores.toml`), logging central, modelos (`Activity`, `DailyLoad`, `Corredor`), 54 testes. Branch `fase-2-configuracao` mergeada; pode ser apagada.
- A `main` local está **1 merge à frente do `origin/main`** — nada foi enviado ainda.
- **Próxima: Fase 3 (Strava/OAuth)** — ver `TASKS.md`. Atenção ao **controle de vazão**: a pesquisa passa de 50 participantes e o limite do Strava é por aplicação.

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
- **Dias contíguos**: `Dia 1` = `start_date` **daquele corredor**; preencher todos os dias; **dia sem corrida → Carga 0** (em `PACE`, Pace/Tempo em branco). Mapa: `dia = (data - start_date).days + 1` → `linha = dia + 1` (é o `Corredor.dia_da_planilha()`).
- **`start_date` = data de entrada do corredor na pesquisa** = o primeiro dia em que o projeto passa a buscar dados dele. É **dado do estudo**, registrado no bloco `[[corredor]]` — nunca inferido de quando o script rodou pela primeira vez (uma execução atrasada excluiria dias de coleta em silêncio). Cada participante tem a sua, então as grades começam em dias diferentes. Consequências:
  - O que o corredor correu **antes** dessa data está fora do escopo, e a Fase 6 deve descartar o que aparecer — `dia <= 0` cairia na linha 1 (cabeçalho) ou acima.
  - A grade também tem teto: `Daily_Data` vai até a linha 366 (365 dias) e `PACE` até a 154 (153 dias). Passado isso, a planilha do corredor acabou — decidir o que fazer (nova cópia? erro?) é assunto da Fase 5.
  - **Depois da planilha gravada, a célula `B2` é a fonte da verdade**, não o cadastro. O app lê `B2` e, se o `corredores.toml` divergir, **aborta aquele corredor em vez de escrever** — mudar a data de uma planilha já preenchida deslocaria a grade inteira. O cadastro só faz o bootstrap da primeira execução.
  - Pode estar no **futuro**: o participante entra na pesquisa semana que vem, o cadastro é feito hoje. Enquanto `hoje < start_date` não há nada para sincronizar.
- **Unidades (Strava → Excel)**: distância m → km; pace = tempo_total_dia ÷ km_dia (min/km); tempo total = Σ `moving_time`.

## Decisões travadas

- **Multi-corredor** (⚠️ *substituiu a decisão anterior de single-athlete*): uma execução atende **todos** os participantes da pesquisa. Onboarding = copiar o template + acrescentar um bloco `[[corredor]]` ao cadastro.
- **Credenciais em duas camadas**: `CLIENT_ID`/`CLIENT_SECRET` são da **aplicação** (um app registrado no Strava para a pesquisa inteira) e ficam no `.env`; o `refresh_token` é de **cada atleta** e fica no cadastro.
- **Cadastro híbrido**: `corredores.toml` (gitignored, contém tokens) declara *quem está na pesquisa*; o SQLite guarda o **estado mutável** — token corrente, última sincronização. O app **nunca reescreve o TOML**: o Strava pode devolver um `refresh_token` novo a cada renovação, e reescrever um arquivo editado à mão destruiria comentários e formatação.
- **Isolamento por corredor**: a falha de um participante (token expirado, planilha aberta no Excel) é registrada e a coleta segue para os demais; o processo sai com código 1 se alguém falhou, para o scheduler enxergar.
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
  models/activity.py, models/daily_load.py, models/corredor.py ✅
  repositories/activity_repository.py  # Fase 4
  utils/config.py ✅, logger.py ✅, auth.py (Fase 3)
  main.py (executável), scheduler.py   # Fase 7
tests/            # pytest
data/             # .gitkeep; *.db, *.xlsx e *.log de runtime são ignorados
corredores.toml   # cadastro dos participantes (gitignored — contém tokens)
```

## Configuração e logging (Fase 2 — já implementados)

- **`utils/config.py`**: `load_config()` → `Config` congelado, em duas camadas.
  - Do `.env` (global): `STRAVA_CLIENT_ID` e `STRAVA_CLIENT_SECRET` são obrigatórias; `CORREDORES_PATH`, `TEMPLATE_PATH`, `DATABASE_PATH`, `LOG_LEVEL`, `LOG_FILE` têm padrão.
  - Do `corredores.toml`: um `Corredor` por bloco `[[corredor]]` — `id` e `refresh_token` e `start_date` obrigatórios; `nome` (padrão = id), `cutover_date` (padrão = start_date) e `excel_path` (padrão = `./data/<id>.xlsx`) opcionais.
  - Validações que evitam corromper dados: **ids únicos** e **planilhas únicas** (dois corredores no mesmo arquivo destruiriam os dados dos dois), `cutover >= start`, e **campo desconhecido é erro** (um `data_inicio` digitado no lugar de `start_date` seria ignorado em silêncio e o participante ficaria sem histórico).
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
- Config em dois arquivos, ambos gitignored e já existentes localmente: `.env` (credenciais do app, **em branco** — preencher na Fase 3) e `corredores.toml` (cópia do exemplo, com participantes fictícios `p001`–`p003`).
- **Cadastrar participante**: acrescentar um bloco `[[corredor]]` ao `corredores.toml` e copiar o template para a planilha dele. Ver `corredores.example.toml`.
- **Ruff**: o `pyproject.toml` não fixa `select`, então valem os padrões da versão instalada (0.16.1), que incluem `DTZ` e `C4`. Considerar fixar um `select` explícito na etapa de Qualidade.
- Nota: a variável `SSL_CERT_FILE` quebrada (sobra de outro projeto) ainda aparece como *warning* do `uv` no shell do Claude — inofensiva, não bloqueia nada.

## Próximo passo — Fase 3 (Strava: OAuth + API)

Em nova branch `fase-3-strava` a partir da `main` (mergear a Fase 2 antes):

- `utils/auth.py`: trocar o `refresh_token` **de cada corredor** por um access token, com renovação automática. O token novo devolvido pelo Strava é **persistido no SQLite**, não no `corredores.toml`.
- `api/strava_client.py`: cliente `httpx` — buscar atleta e atividades, paginação, tratamento de erros.
- Filtrar apenas `type == "Run"`; mapear o JSON para `Activity` (atenção ao `start_date_local`).
- Passar `after=` (epoch) na busca de atividades, para não paginar o histórico inteiro do atleta. **A data é `cutover_date`, não `start_date`** — numa planilha adotada, o trecho entre as duas já foi preenchido à mão e o app não vai reescrevê-lo. Para corredor novo as duas coincidem.
- ⚠️ **Controle de vazão é requisito, não otimização.** O limite do Strava é **por aplicação** (padrão: 100 req/15min, 1000/dia) e a pesquisa passa de 50 participantes — o teto é dividido por todos. A ~2 requisições por corredor (renovar token + buscar atividades), 100 participantes gastam ~200 requisições por execução: cabe no limite diário, mas **estoura a janela de 15 minutos**. Daí decorre:
  - Sincronização **incremental**: guardar a data da última sincronização por corredor e usá-la no `after=`, em vez de varrer desde o corte toda vez.
  - **Pausa entre chamadas** e respeito ao `429`/`X-RateLimit-Usage` da resposta.
  - **Retomável**: se a execução for interrompida no corredor 60, a próxima continua dali em vez de recomeçar.
- Referência detalhada: `TASKS.md` › Fase 3.
