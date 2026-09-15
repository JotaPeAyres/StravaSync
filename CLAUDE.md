# CLAUDE.md

Contexto do projeto para o Claude Code. **Leia isto antes de agir.** Documento vivo — atualize conforme o projeto avança.

## O que é

**StravaSync**: app Python que sincroniza corridas do Strava para planilhas Excel de **carga de treino (ACWR / EWMA)**, no contexto de uma **pesquisa com múltiplos corredores**. A cada execução percorre **todos os participantes cadastrados**: agrega as corridas de cada um **por dia**, alimenta a planilha dele **preservando fórmulas/gráficos**, e mantém um SQLite com os dados ricos por atividade (dedupe + estatísticas futuras).

## Estado atual

- **Fases 1 a 6: concluídas e na `main`.** Estrutura, configuração em duas camadas (`.env` +
  `corredores.toml`), OAuth/API do Strava, banco (`activities`, schema v3), `ExcelService`
  (com adoção de planilhas preenchidas à mão) e `SyncService`/`main.py` amarrando tudo —
  ver as seções por fase abaixo para os detalhes de cada uma. 309 testes, tudo com
  `httpx.MockTransport` e o template real do Excel — **nenhuma credencial real foi usada**.
- **Fase 7 (Scheduler): concluída e na `main`.** Entrega: `src/scheduler.py` (wrapper para
  agendador externo, delega para `main.main()`), schema v4 (`corredor_state.falhas_consecutivas`),
  ordenação de `sincronizar_todos` por corredor sincronizado há mais tempo (rotação quando a
  cota corta a execução no meio) e alerta `CRITICAL` a cada múltiplo de
  `ALERTA_FALHAS_CONSECUTIVAS` falhas seguidas de um mesmo corredor. Ver "Fase 7 (Scheduler) —
  como ficou" abaixo.
- **Fase 8 (Testes): concluída e na `main`.** 324 → 402 testes: o item central foi um teste
  E2E com a pilha 100% real (`StravaClient`/`RateLimiter`/`ExcelService`/SQLite) para
  múltiplos corredores na mesma execução — até então nenhum teste provava que o
  `RateLimiter`/`StravaClient` compartilhados funcionavam de fato entre corredores, nem que a
  falha de um não contaminava os demais. Cobertura de linha em 97% (`pytest --cov=src`, sem
  gate de CI — o número-alvo é da fase Qualidade). Ver `TASKS.md` › Fase 8 para o detalhe por
  arquivo.
- **Revisão de código pós-Fase 8**: um code review (`/code-review high`) sobre o projeto
  inteiro (não um diff — `main` estava limpa) achou 10 pontos, corrigidos na branch
  `fase-8-correcoes-review`: dois de correção real (`SyncService._calcular_after` não dava
  folga de fuso na primeira sincronização de um corredor, e nada descartava atividades
  anteriores ao `start_date` antes de persistir — as duas juntas podiam, em fusos != 0, perder
  uma corrida para sempre ou duplicar um dia de território manual), mais classificação de
  `RevokedTokenError` sem checar o `code` do erro, um `_uso_curto` do `RateLimiter` que não
  zerava depois de um 429 (dobrando a espera seguinte), paginação da API falsamente
  interrompida num múltiplo exato de `MAX_PAGINAS`, uma consulta N+1 na agregação diária, e
  três limpezas de duplicação/código morto.
- **Pendência bloqueante para validar de verdade** (Fases 3 a 7 dependem do Strava real):
  registrar o app no Strava (*Authorization Callback Domain* = `localhost`) e preencher
  `STRAVA_CLIENT_ID`/`STRAVA_CLIENT_SECRET` no `.env`, hoje vazios. Não bloqueia o
  desenvolvimento — tudo até aqui foi validado com `httpx.MockTransport` e o template real
  do Excel.
- **Próxima: Fase 8 (Testes)** — ver `TASKS.md`.

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
- **Inscrição OAuth manual (opção A)**: o participante abre o link de autorização que enviamos, aprova, e **nos devolve o `code`** (ou a URL inteira de retorno). Nós trocamos por token. Sem servidor, sem página hospedada. Detalhes que fazem isso funcionar:
  - `redirect_uri = http://localhost/exchange_token` (o *Authorization Callback Domain* do app do Strava é `localhost`). A página **não carrega** — e não precisa: o `code` aparece na barra de endereço, e é isso que o participante copia.
  - **`state = <id do corredor>`** na URL de autorização. O Strava devolve o `state` intacto, então a URL colada se auto-identifica — com 50+ participantes, isso evita atribuir o token de um ao cadastro de outro.
  - Aceitar **a URL inteira colada**, não só o `code`: é o que o participante tem à mão, e extrair `code`/`state` dela é trivial.
  - ⚠️ **O `code` é de uso único e de vida curta.** Como ele viaja por mensagem até nós, a troca tem que acontecer assim que chega; código expirado precisa de erro explícito ("peça novo link"), não de falha genérica de autenticação.
- **Escopo `activity:read_all`, validado na inscrição.** A tela de consentimento do Strava tem caixas de seleção: o participante pode aprovar e desmarcar o acesso a atividades privadas. Sem conferir, isso só apareceria meses depois — e obrigaria a procurar a pessoa de novo. Trocar de escopo depois obriga a reautorizar **todos**.
  - ⚠️ **Correção descoberta na Fase 3**: o Strava **não devolve `scope`** na resposta da troca (só `token_type`, `expires_at`, `expires_in`, `refresh_token`, `access_token`, `athlete`). O escopo vem **apenas na query da URL de retorno**. Por isso "aceitar a URL inteira colada" deixou de ser conveniência e virou **requisito de correção**: quem cola só o `code` não traz a informação, e a inscrição **recusa** (`--sem-verificar-escopo` força, gravando `scope = NULL`, visível no `inscricao estado`).
  - A validação acontece **antes** da troca: o `code` é de uso único, e gastá-lo para depois recusar exigiria outro link de qualquer jeito.
  - O código lê `scope` da resposta se algum dia ele aparecer — a URL é só o fallback. **Confirmar contra a API real** na primeira inscrição com o app registrado.
- **Um único `client_id` para o estudo inteiro.** Registrar um segundo app no Strava invalidaria de uma vez os tokens de todos os participantes.
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
  api/strava_client.py                 # ✅ Fase 3
  api/rate_limiter.py                  # ✅ Fase 3
  services/activity_service.py         # ✅ Fase 3 (+ aggregate_daily na Fase 6)
  services/sync_service.py             # ✅ Fase 6
  services/excel_service.py            # ✅ Fase 5
  services/database_service.py         # ✅ Fase 3 (schema v1) — v2 na Fase 4, v3 na Fase 5.5
  services/adocao_service.py           # ✅ Fase 5.5
  models/activity.py, models/daily_load.py, models/corredor.py, models/registro_historico.py ✅
  repositories/activity_repository.py  # ✅ Fase 4 (+ importar_historico na Fase 5.5)
  repositories/corredor_state_repository.py  # ✅ Fase 3
  repositories/escrita_excel_repository.py   # ✅ Fase 5.5
  utils/config.py ✅, logger.py ✅, auth.py (Fase 3)
  main.py (executável), scheduler.py   # Fase 7
tests/            # pytest
data/             # .gitkeep; *.db, *.xlsx e *.log de runtime são ignorados
corredores.toml   # cadastro dos participantes (gitignored — contém tokens)
```

## Configuração e logging (Fase 2 — já implementados)

- **`utils/config.py`**: `load_config()` → `Config` congelado, em duas camadas.
  - Do `.env` (global): `STRAVA_CLIENT_ID` e `STRAVA_CLIENT_SECRET` são obrigatórias; `CORREDORES_PATH`, `TEMPLATE_PATH`, `DATABASE_PATH`, `LOG_LEVEL`, `LOG_FILE` têm padrão. A Fase 3 acrescentou `STRAVA_TIMEOUT_S` (20), `STRAVA_PAUSA_ENTRE_CHAMADAS_S` (1) e `STRAVA_RESERVA_DE_VAZAO` (10) — campos com padrão, **no fim do dataclass**. URLs, `redirect_uri` e escopo ficam **constantes no código**: se a URL do link e a da troca divergirem, o Strava recusa de um jeito difícil de diagnosticar.
  - Do `corredores.toml`: um `Corredor` por bloco `[[corredor]]` — `id` e `refresh_token` e `start_date` obrigatórios; `nome` (padrão = id), `cutover_date` (padrão = start_date) e `excel_path` (padrão = `./data/<id>.xlsx`) opcionais.
  - Validações que evitam corromper dados: **ids únicos** e **planilhas únicas** (dois corredores no mesmo arquivo destruiriam os dados dos dois), `cutover >= start`, e **campo desconhecido é erro** (um `data_inicio` digitado no lugar de `start_date` seria ignorado em silêncio e o participante ficaria sem histórico).
  - Erros são **acumulados** e levantados de uma vez em `ConfigError` — nada de corrigir o `.env` uma variável por vez.
  - **Caminhos relativos partem de `PROJECT_ROOT`**, não do CWD (o scheduler da Fase 7 pode rodar de qualquer lugar).
  - O ambiente tem precedência sobre o `.env` (permite sobrescrever em CI/agendador).
  - Segredos ficam fora do `repr`; use `config.safe_summary()` para logar a config.
- **`utils/logger.py`**: `setup_logging(level, log_file, force=False)` é **idempotente**; chamada no `main.py` e no `inscricao.py`. Demais módulos usam `get_logger(__name__)`. Arquivo rotativo em UTF-8 (acentos); `httpx`/`httpcore`/`urllib3` silenciados em WARNING.
- **`Activity.date` é horário LOCAL e ingênuo** (o `start_date_local` do Strava). A planilha é indexada pelo dia local: corrida às 22h de 01/01 vai para a linha de 01/01, não a de 02/01 em UTC. A Fase 3 deve ler `start_date_local`, **não** `start_date`.
- Conversões de unidade moram nos modelos: `Activity.distance_km`, `Activity.day`, `DailyLoad.pace_min_km`, `DailyLoad.tempo_total` (timedelta), `DailyLoad.is_rest_day`, `DailyLoad.dia_de_descanso()`.

## Ambiente e comandos

- Python **3.12.7**; `.venv` já criado (gitignored). `uv` **0.12.2** no PATH.
- `uv sync` — instala deps · `uv run pytest` · `uv run ruff check .` · `uv run python -m src.main`.
- **Inscrição (Fase 3)**: `uv run python -m src.inscricao link <id>` · `... trocar "<url colada>"` · `... estado [id]` (sai com 1 se alguém está pendente).
- Config em dois arquivos, ambos gitignored e já existentes localmente: `.env` (credenciais do app, **ainda em branco** — bloqueia qualquer teste real) e `corredores.toml` (cópia do exemplo, com participantes fictícios `p001`–`p003`).
- **Cadastrar participante**: acrescentar um bloco `[[corredor]]` ao `corredores.toml`, copiar o template para a planilha dele e rodar `inscricao link`/`trocar`. Ver `corredores.example.toml`.
- **Fumaça sem credenciais** (o ambiente tem precedência sobre o `.env`): `$env:STRAVA_CLIENT_ID = "000000"; $env:STRAVA_CLIENT_SECRET = "fake"; uv run python -m src.inscricao link p001` — exercita `config → banco → CLI` ponta a ponta.
- **Ruff**: o `pyproject.toml` não fixa `select`, então valem os padrões da versão instalada (0.16.1), que incluem `DTZ` e `C4`. Considerar fixar um `select` explícito na etapa de Qualidade.
- Nota: a variável `SSL_CERT_FILE` quebrada (sobra de outro projeto) ainda aparece como *warning* do `uv` no shell do Claude — inofensiva, não bloqueia nada.

## Fase 3 (Strava: OAuth + API) — como ficou

### Módulos

- **`utils/errors.py`** — hierarquia única, sem importar nada do projeto (evita ciclo `auth ↔ strava_client`). Raiz `StravaSyncError(RuntimeError)`; `ConfigError` passou a herdar dela. Mensagens de erro carregam **a ação de recuperação** (`python -m src.inscricao link p001`): com 50+ participantes, a mensagem é o runbook.
- **`api/rate_limiter.py`** — uma instância por execução, no cliente único.
- **`api/strava_client.py`** — **stateless quanto a token**: todo método recebe o `access_token`. É o que permite um cliente só (e um limitador só) servindo todos os corredores. Um cliente por corredor derrotaria o controle de vazão.
- **`utils/auth.py`** — a política: link, parse do retorno, escopo, precedência do token, renovação, revogação.
- **`services/database_service.py`** — conexão + migração por `PRAGMA user_version` (hoje v1: `corredor_state`). A Fase 4 acrescenta `if versao < 2:` sem tocar no que já foi gravado.
- **`repositories/corredor_state_repository.py`** — `commit()` **por corredor**, não no fim: é o que torna a execução retomável.
- **`src/inscricao.py`** — CLI de operador (`link` / `trocar` / `estado`), separada do `main.py`, que é a rotina do agendador.

### Fluxo do token (a precedência mora numa função só)

`auth.refresh_token_corrente()`: SQLite tem token → usa ele; senão → `corredores.toml` (bootstrap). `obter_access_token()` é o ponto de entrada: pula corredor marcado para reinscrição **sem gastar requisição**, reaproveita access token ainda válido (margem 5 min, **zero requisições**), e ao renovar **grava antes de devolver** — se o processo morrer no meio, o token antigo pode já ter sido invalidado pela rotação do Strava.

⚠️ **Armadilha de suporte**: depois da primeira renovação, editar `refresh_token` no TOML não tem efeito. O log diz a origem (`SQLite` | `cadastro`), o `inscricao estado` mostra o que está gravado, e reinscrever pelo `trocar` é o caminho oficial.

### Distinções que não podem ser confundidas

| Situação | Sinal | Ação |
|---|---|---|
| Access token vencido (dura 6h) | `401` na API | `ExpiredAccessTokenError` → renova e repete **uma** vez (`auth.chamar_renovando`) |
| Token revogado | `400` em `/oauth/token`, corpo `RefreshToken/invalid` | `RevokedTokenError` → marca reinscrição |
| `401` de novo após renovar | — | `RevokedTokenError` (defesa contra laço) |
| `code` morto | `400`, corpo `AuthorizationCode/invalid` | `ExpiredCodeError` ("peça link novo") |

A classificação sai do **corpo**, não só do status. Marcar reinscrição num mero access token vencido tiraria o participante da coleta por engano.

### Vazão

Espaça as chamadas por relógio **monotônico**; lê `X-RateLimit-Limit`/`X-RateLimit-Usage` de **toda** resposta, inclusive as de `/oauth/token` (que também consomem cota). Cabeçalho ausente ou malformado nunca levanta — um cabeçalho quebrado do Strava não pode derrubar a coleta.

- **Janela curta (100/15min) → espera** a virada do quarto de hora.
- **Cota diária (1000/dia) → `QuotaExhaustedError` e encerra**: dormir horas num job agendado é inaceitável, e os corredores restantes ficam *pendentes*, não *falhos*.
- **Nada do limitador persiste entre execuções, de propósito**: a primeira resposta já o reposiciona pelos cabeçalhos. Um contador local seria uma segunda fonte de verdade.

### O `after=` (armadilha mais séria da fase)

O `after=` filtra por `start_date` em **UTC**, não pelo horário local. E usar o relógio da última execução perderia **para sempre** uma corrida de domingo enviada na terça — daí as duas colunas separadas: `ultima_sincronizacao` (quando rodamos) e `ultimo_evento_em` (o `start_date` UTC mais recente visto, que só avança). A fórmula, implementada em `SyncService._calcular_after` na Fase 6, é `max(meia-noite UTC de cutover_date, ultimo_evento_em − 2 dias)`; **a data-base é `cutover_date`, não `start_date`**.

⚠️ **Existem dois `ultimo_evento_em`** — não confundir. `corredor_state.ultimo_evento_em` (Fase 3, escrito por `CorredorStateRepository.registrar_sincronizacao`) e `ActivityRepository.ultimo_evento_em()` (Fase 4, `MAX(start_date_utc)` sobre `activities`). O `SyncService` usa **o da Fase 4** para o `after=` — é o mais forte dos dois, porque só avança quando a atividade *de fato foi persistida* (ver a razão em "Fase 4 › ActivityRepository" abaixo). O de `corredor_state` continua sendo gravado (via `registrar_sincronizacao`, no fim de `sincronizar()`) só para auditoria/monitoramento — a Fase 7 pode querer ler `ultima_sincronizacao` de lá.

### Consumo das exceções no `main.py` — implementado na Fase 6

```
cria UMA vez: DatabaseService, RateLimiter, StravaClient
por corredor: marcado precisa_reinscricao? → WARNING e pula (0 requisições)
  except QuotaExhaustedError:  loga os pendentes e BREAK   # não é falha de corredor
  except RevokedTokenError:    marca reinscrição; falhas += 1; continua
  except (AuthorizationError, StravaError): falhas += 1; continua
```

A pré-checagem de `precisa_reinscricao` é o detalhe fácil de perder: ela mora em
`main.py::_precisa_reinscricao`, **fora** do bloco `try`, porque só assim um corredor já
conhecidamente pendente não conta como falha *desta* execução — a mesma `RevokedTokenError`
levantada *durante* uma tentativa (descoberta agora) conta.

### Pendências e riscos vivos

- ⚠️ **Confirmar contra a API real** que a resposta da troca não traz `scope` (o código já é defensivo).
- **`calories` não existe** em `/athlete/activities` — só no endpoint por atividade, que custaria +1 requisição por corrida. Fica `None`; documentado no código.
- **`VirtualRun` (esteira/Zwift) é descartada**; `TrailRun` passa (o `type` dela é `"Run"`). Os tipos descartados vão para o log em DEBUG — decidir na Fase 6, com número real na mão, se esteira conta como carga.
- **`Activity` continua sem `corredor_id`**: a posse é atributo de persistência e vira coluna de `activities` na Fase 4. Se um dia precisar, entra como `corredor_id: str | None = None` no fim, sem quebrar teste nenhum.
- Referência detalhada: `TASKS.md` › Fase 3.

## Fase 4 (Banco) — como ficou

### Schema v2 — `activities`

Migração **aditiva** por `PRAGMA user_version`: `if versao < 1:` cria
`corredor_state`, `if versao < 2:` cria `activities`. Recriar a v1 obrigaria os
50+ participantes a reautorizar um por um.

- **`UNIQUE (corredor_id, activity_id)` é o dedupe**, e é composto de propósito:
  uma chave global faria a sincronização de um participante **capturar a linha de
  outro** se duas inscrições apontarem para a mesma conta do Strava. Com a
  composta, o pior caso é linha duplicada (visível), não dado atribuído à pessoa
  errada.
- **`activity_id` é anulável** e existe a coluna `origem` (`strava` | `manual`) —
  acomoda o histórico manual. ⚠️ O `ON CONFLICT (corredor_id, activity_id)`
  **não dispara** com `activity_id NULL`: reimportar histórico manual
  duplicaria. Resolvido na Fase 5.5 com um índice **parcial** em v3 — ver
  seção própria abaixo.
- **Sem FOREIGN KEY** para `corredor_state`: aquilo é cache do OAuth, não
  cadastro (quem existe na pesquisa é o `corredores.toml`). A adoção (Fase
  5.5) importa histórico de corredores que ainda não autorizaram; uma FK
  viraria erro.
- **`pace` não é coluna.** É função de distância e tempo, e o pace da pesquisa é o
  **diário** (Σ tempo ÷ Σ km), que não é a média dos paces por atividade. A regra
  geral: guardamos o que a fonte afirma (`average_speed`), não o que calculamos.

### As três datas (não podem ser confundidas)

| Coluna | Conteúdo | Para quê |
|---|---|---|
| `date_local` | ISO **sem fuso**, do `start_date_local` | hora do treino; desempate na ordenação |
| `day` | `YYYY-MM-DD` | **a linha da planilha** |
| `start_date_utc` | ISO UTC, do `start_date` | marca d'água do `after=` |

⚠️ `date_local` usa **`para_texto_local` / `de_texto_local`**, nunca
`para_texto` / `de_texto`: estes assumem UTC no ingênuo e gravariam "22h UTC"
para uma corrida das 22h locais, deslocando-a de dia. É o erro mais fácil de
cometer aqui, porque o helper errado existe e parece certo.

`Activity` ganhou **`start_date_utc`** (opcional, no fim). A leitura no
`ActivityService` é **não fatal**, ao contrário do `start_date_local`: sem ela o
`after=` cai no `cutover_date` e a execução repagina histórico — custa cota, não
corrompe dado.

### `ActivityRepository`

`existe` / `salvar` / `salvar_muitas` / `por_dia` / `por_periodo` /
`ultimo_evento_em`. Nomes em português, como o `CorredorStateRepository`.

- **Upsert, não "pula se já existe"**: a janela de 2 dias reencontra a corrida, e
  um atleta que corrige a distância depois do upload teria a correção descartada.
  A regra do `COALESCE`: use onde `None` é "não perguntamos" (`calories`, que a
  listagem nunca traz); sobrescreva onde `None` é "o atleta não tem esse dado"
  (FC, cadência) — blindar estes tornaria impossível corrigir valor errado.
- **`INSERT OR REPLACE` é proibido** (comentado no código): é DELETE + INSERT,
  zera `criado_em`, troca o id e apaga colunas omitidas.
- **`ResultadoGravacao.dias_afetados`** inclui o **dia antigo** quando o atleta
  muda a data da corrida no Strava — sem isso a linha antiga ficaria com carga
  fantasma e nada apontaria para lá. O evento também vai a WARNING.
- **Lote atômico por corredor**, com `commit()` no fim: logo depois da gravação a
  marca d'água avança, e lote pela metade + marca d'água avançada = corridas
  perdidas para sempre. Por isso `_executar` faz **`rollback()`** antes de
  levantar — senão as linhas parciais entrariam pelo `commit()` do corredor
  seguinte.
- **`ultimo_evento_em` sai do banco**, não do retorno da API: assim a marca
  d'água nunca ultrapassa o que foi persistido. Linhas manuais
  (`start_date_utc IS NULL`) são ignoradas pelo `MAX`.
- **`existe` não é usado na sincronização** — o dedupe é o índice. Fica como
  afordância de diagnóstico, e isso está no docstring para não parecer órfão.
- **Data ilegível em `date_local` levanta**, divergindo de propósito do
  `corredor_state` (onde vira `None`): ali custa uma página de API, aqui faria a
  corrida **sumir da agregação** e subnotificar a carga sem sinal nenhum.

### O que a Fase 6 fez com essas pendências

Agregação a partir do banco, reescrita de `dias_afetados` e detecção de atividade apagada —
todas implementadas em `SyncService`; ver a seção própria "Fase 6" abaixo.

## Fase 5 (Excel) — como ficou

### `ExcelService.sincronizar(corredor, daily_loads)`

Um método só: recebe o `Corredor` (para `start_date`, `dia_da_planilha` e
`excel_path`) e os `DailyLoad` a gravar, devolve `ResultadoExcel` (dias
escritos por aba, descartados e além do teto). **Não conhece o SQLite nem a
API** — quem agrega (Fase 6) decide o que mandar; isto aqui só escreve.

- **Abre com `openpyxl.load_workbook()` sem `data_only`**: assim a fórmula fica
  como fórmula (string) e é regravada tal como estava — é o Excel, ao abrir,
  quem recalcula os valores. Carregar com `data_only=True` teria devolvido o
  **valor em cache** da última vez que alguém abriu no Excel, e regravar isso
  teria **apagado a fórmula** da coluna.
- **`B2` de `Daily_Data` é a fonte da verdade** depois da primeira escrita:
  `B2` vazio → bootstrap, seguem escrita normal (o `Dia 1` grava `B2` sozinho).
  `B2` preenchido e diferente do `start_date` do cadastro → `DataBaseDivergenteError`,
  **nada é escrito** para aquele corredor. `B2` ilegível (texto que não é data)
  recebe o mesmo tratamento — confere a planilha e cadastro antes de seguir.
- **Grade tem teto por aba, e cada aba é independente**: `Daily_Data` até a
  linha 366, `PACE` até a 154 (~5 meses — estoura bem antes de `Daily_Data`).
  Um dia além do teto é **descartado só naquela aba** — `WARNING` no log, sem
  abortar o corredor nem perder o dado (que segue íntegro no SQLite). Decisão
  do usuário: criar uma segunda planilha automaticamente fica para quando o
  problema aparecer de verdade, não antecipado aqui.
- **Dia anterior ao `Dia 1` (`dia <= 0`) também é descartado com `WARNING`** —
  defesa a mais, redundante com o filtro que a Fase 6 (`SyncService`) deve
  aplicar antes de chamar o Excel; nenhuma das duas pontas confia sozinha na
  outra.
- **Dia de descanso**: `Daily_Data!C` e `PACE!C` recebem `0.0` (a grade é
  contígua — nunca célula vazia); `PACE!D`/`E` (Pace/Tempo) ficam **em
  branco** (`None`), não zero — um pace de "0 min/km" seria lido como dado,
  não como ausência dele.
- **Grava em `.tmp.xlsx` no mesmo diretório e troca com `os.replace`**
  (atômico no mesmo filesystem): uma falha no meio do `wb.save()` não pode
  corromper a planilha do corredor pela metade. `os.replace` falhando com
  `PermissionError` (destino aberto no Excel, trava do Windows) vira
  `PlanilhaEmUsoError` — mensagem específica, e o arquivo original nunca é
  tocado até a troca ter sucesso.
- **Lista de `daily_loads` vazia não abre nem salva o arquivo** — evita
  reescrever (e arriscar `PlanilhaEmUsoError`) uma planilha sem nenhuma
  mudança de conteúdo real.
- ⚠️ **Datas voltam do openpyxl como `datetime`, nunca `date`**, mesmo em
  célula formatada como data — quem ler `B2`/coluna B para comparar precisa de
  `.date()` primeiro (armadilha nos testes, documentada lá).
- Testado contra o **template real** (`Cópia de Planilha_carga_corrida.xlsx`),
  não um mock: round-trip de fórmulas (`D2`/`F2`/`G2`/`H2`) e dos *named
  ranges* (`DatasDiarias`, `ACWR_EWMA`, ...) confirmado — nenhum é alterado
  pela gravação.

### Erros novos em `utils/errors.py`

`PlanilhaError` (raiz) → `PlanilhaNaoEncontradaError` (onboarding não fez a
cópia do template), `PlanilhaEmUsoError` (arquivo aberto em outro programa,
na leitura ou na escrita) e `DataBaseDivergenteError` (`B2` × cadastro). Sem
depender de `openpyxl` nem de nada do projeto, como o resto do módulo.

### Pendências que a Fase 5 empurra adiante

- ~~Fase 6 decide o que mandar para `sincronizar()`~~ — feito: `SyncService`
  agrega do banco, reescreve `dias_afetados` e orquestra a baseline de edição
  humana (ver "Fase 6" abaixo).
- Se um dia a pesquisa decidir abrir uma segunda planilha por corredor quando
  a grade esgotar, o ponto de entrada é o `WARNING` de "além do limite" — hoje
  só logado, o dado permanece no SQLite.

## Fase 5.5 (Adoção de planilhas já preenchidas) — como ficou

### Schema v3

Duas estruturas aditivas, no mesmo `if versao < 3:`:

- **Índice parcial** `UNIQUE (corredor_id, day) WHERE activity_id IS NULL` —
  resolve a lacuna que a Fase 4 já tinha previsto: o `UNIQUE (corredor_id,
  activity_id)` da v2 não dispara com `activity_id NULL` (o SQLite trata cada
  `NULL` como distinto), então sem este índice rodar a adoção duas vezes
  duplicaria o período manual inteiro a cada vez.
- **Tabela `excel_escritas`** (`corredor_id, day, carga_km, tempo_total_s,
  atualizado_em`) — o último valor que o **próprio app** escreveu em cada dia.
  Existe só para uma comparação: célula atual × este valor. Se divergirem, foi
  um humano; se baterem, é o app escrevendo de novo (rotina).

### `ExcelService.ler_historico_manual(corredor)`

Lê `[start_date, cutover_date)` de volta como `RegistroHistorico` (day,
carga_km, `tempo_total_s: int | None`). Corredor novo (`start_date ==
cutover_date`) devolve vazio sem abrir o arquivo.

- **`tempo_total_s=None`** quando `PACE` não cobre o dia (grid menor que
  `Daily_Data`, ou célula em branco dentro do grid) — a distância ainda entra
  (enriquece a pesquisa), só sem como calcular o pace daquele dia.
- **Lacuna (linha com `B` em branco) não é erro** — uma planilha mantida à
  mão antes do app existir pode ter buracos reais. Vai para
  `HistoricoManual.lacunas`, não interrompe a leitura dos outros dias.
- **`GradeDesalinhadaError` é erro**, e é outra coisa: a linha *tem* data,
  mas ela não bate com o que a posição implica (`dia = (data -
  start_date).days + 1`). Sinal de linha inserida/apagada à mão — dali para
  frente todo mapeamento data→linha estaria errado. Correção é humana, na
  planilha; o código não tenta adivinhar o realinhamento.
- Célula de carga não-numérica (texto solto tipo `"descanso"`) vira `0.0` com
  `WARNING`, em vez de abortar a adoção inteira por causa de um dia.
- Reusa `_validar_data_base` (mesma checagem de `B2` do `sincronizar`) — não
  faz sentido importar histórico de uma planilha cuja base já diverge do
  cadastro.

### `ActivityRepository.importar_historico`

Upsert dedicado, com `_SQL_UPSERT_MANUAL` mirando o índice parcial em vez do
`UNIQUE (corredor_id, activity_id)` da Fase 4.

- **Só `RegistroHistorico.tem_corrida` vira linha** — dia de descanso nunca é
  uma atividade, nem quando a sincronização é do Strava (que também nunca
  cria uma linha para um dia sem corrida).
- **`start_date_utc` fica `NULL` de propósito**: não há hora exata de uma
  corrida digitada à mão, e `ultimo_evento_em` (a marca d'água do `after=`)
  já ignora linhas assim — histórico manual não pode empurrar a busca do
  Strava.
- **`tempo_total_s=None` vira `moving_time_s=0`**, com `WARNING` — a distância
  entra, mas fica registrado que o pace daquele dia é indeterminado.

### Detecção de edição humana — `ExcelService.sincronizar(..., ultima_escrita=...)`

Parâmetro novo, opcional e retrocompatível: sem ele, o comportamento é
idêntico ao da Fase 5 (todo dia é escrito incondicionalmente).

- **A baseline é reconstruída como um `DailyLoad`** antes de comparar — não
  dá para comparar `tempo_total_s` cru contra a célula, porque em dia de
  descanso a célula de `PACE` fica **em branco**, não `0`. Reaproveitar
  `DailyLoad.tempo_total`/`is_rest_day` evita que todo dia de descanso pareça
  "editado à mão" na segunda sincronização.
- **Dia anterior ao `cutover_date` nunca é escrito**, ponto — nem chega a
  olhar a baseline. Esse período é território do histórico manual;
  `dias_sob_gestao_manual` conta quantos foram descartados assim.
- **Dia editado à mão é preservado, não sobrescrito** (`dias_preservados`) —
  e a preservação é **permanente**: como o dia não entra em
  `ResultadoExcel.novas_escritas`, a baseline gravada continua sendo a
  antiga, e a próxima sincronização vai flagrar a mesma divergência de novo.
  Não existe hoje um jeito de "destravar" um dia — não estava no escopo
  documentado, e adicionar um teria sido antecipar requisito não pedido.
- **`novas_escritas` segue `Daily_Data`, não `PACE`**: o teto de `PACE` é bem
  menor e mais fácil de estourar; um dia sem `PACE` ainda é um dia gravado
  (baseline não pode ficar "incompleta" por isso).
- Quem guarda e devolve `ultima_escrita`/`novas_escritas` entre execuções é a
  Fase 6 — `ExcelService` continua sem tocar o SQLite, de propósito.

### `AdocaoService.adotar(corredor)`

A costura: `ExcelService.ler_historico_manual` → filtra o que entra no
relatório (lacunas, dias sem tempo) → `ActivityRepository.importar_historico`
→ `ResultadoAdocao`. **Nunca escreve no Excel** — esse histórico já está lá;
só o SQLite precisa ser alimentado. Idempotente: rodar de novo com a planilha
inalterada reimporta o mesmo período e atualiza as mesmas linhas (via o
índice parcial), sem duplicar.

### Erros novos em `utils/errors.py`

`GradeDesalinhadaError(PlanilhaError)` — linha da grade com data que não bate
com a posição dela (ver `ler_historico_manual` acima).

### `Activity.id` virou `int | None`

Era `int` obrigatório desde a Fase 3. Histórico manual não tem `activity_id`
do Strava, e o valor precisa sobreviver ao ciclo completo — gravado como
`NULL`, lido de volta via `_para_modelo` em `por_dia`/`por_periodo` — sem
violar o tipo declarado. Campo movido para depois dos obrigatórios no
dataclass (`id: int | None = None`, logo após `elapsed_time_s`); nenhum call
site quebrou porque todos já construíam `Activity` por *keyword arguments*.
Um teste da própria Fase 4 (`test_atividades_manuais_sem_activity_id_convivem`)
já exercitava `id=None` — o tipo só passou a refletir o que já era verdade.

### O que a Fase 6 decidiu sobre essas pendências

- **Quando rodar a adoção**: **a cada execução**, para todo corredor com
  `adota_planilha_existente`. É idempotente (índice parcial da Fase 5.5) e o
  custo é ler de volta um período limitado (≤365 dias); uma marca de "já
  adotado" foi cogitada e descartada por criar estado novo para economizar
  I/O local irrelevante — ver `SyncService._adotar_se_aplicavel`.
- **Baseline em volta de `sincronizar()`**: feito em `SyncService.sincronizar`
  — carrega antes, `ExcelService.sincronizar(..., ultima_escrita=baseline)`,
  grava `resultado_excel.novas_escritas` depois.
- **Sem jeito de destravar um dia preservado** continua **verdade** — não
  mudou na Fase 6, e não estava no escopo dela.

## Fase 6 (Regra de Negócio) — como ficou

### `SyncService.sincronizar(corredor)` — um corredor por chamada

Ordem dos passos, e por quê:

1. **Adoção** (`_adotar_se_aplicavel`), se `corredor.adota_planilha_existente`. Primeiro porque
   é local (sem rede) e não interage com o resto — se a planilha manual estiver corrompida
   (`GradeDesalinhadaError`), o corredor falha aqui, antes de gastar uma requisição.
2. **`after=`** (`_calcular_after`) — a fórmula travada: `max(meia-noite UTC de cutover_date,
   ultimo_evento_em − 2 dias)`. `ultimo_evento_em` vem de `ActivityRepository`, não de
   `corredor_state` (ver a nota "dois `ultimo_evento_em`" acima).
3. **Busca + renovação** via `auth.chamar_renovando` — reaproveita a política inteira da Fase 3
   (renova uma vez em 401, marca reinscrição em revogação) sem duplicar nada aqui.
4. **Filtra e converte** (`_converter_tolerando_falhas`): `only_runs` primeiro, depois
   `to_activity` **um por um** — uma atividade malformada vira WARNING e é descartada, o lote
   segue. Abortar o corredor inteiro por causa de uma corrida com campo faltando seria
   desproporcional ao mesmo princípio de isolamento que rege a execução como um todo.
5. **Grava** (`ActivityRepository.salvar_muitas`) — upsert, dedupe, `dias_afetados`; nada novo,
   só chamado.
6. **Detecta apagadas** (`_detectar_apagadas`) — zero requisição extra, usa o `payloads` já
   buscado no passo 3.
7. **Decide os dias a escrever** (`_dias_para_escrever` — ver seção própria abaixo).
8. **Agrega do banco**: `ActivityService.aggregate_daily(dia, ActivityRepository.por_dia(...))`
   para cada dia decidido no passo 7 — nunca a partir do `payloads` cru.
9. **Escreve o Excel** com a baseline carregada/salva em volta (`EscritaExcelRepository`).
10. **Atualiza `corredor_state`** (`ultima_sincronizacao`, e `ultimo_evento_em` como espelho de
    auditoria — a fonte de verdade continua sendo `ActivityRepository`).

### `_dias_para_escrever`: por que não basta `dias_afetados`

Este é o ponto mais fácil de fazer errado na fase. `dias_afetados` (Fase 4) só contém dias que
**mudaram no banco nesta execução** — uma corrida nova, uma corrida corrigida, ou o dia antigo
de uma corrida que mudou de data. Um dia sem corrida nenhuma (um descanso comum, ou
simplesmente "hoje") **nunca** aparece ali. Se `SyncService` escrevesse só `dias_afetados`, a
grade do Excel — que os named ranges (`DatasDiarias`, `ACWR_EWMA`, ...) pressupõem contígua via
`COUNTA` — ficaria com buracos permanentes.

A solução reaproveita a baseline que a Fase 5.5 já persiste (`EscritaExcelRepository`): o maior
dia já escrito (`max(baseline, default=cutover_date - 1 dia)`) diz onde a planilha parou. A
janela de escrita é `[max(ultimo_dia_escrito + 1, cutover_date), hoje]`, contígua, **em união**
com `dias_afetados` (que alcança dias fora dessa janela recente).

Consequência que vale registrar: isso torna a escrita **self-healing** entre execuções. Se o
agendador (Fase 7, ainda não existe) pular alguns dias, a próxima execução simplesmente
completa a janela inteira de uma vez — não há necessidade de um mecanismo de recuperação à
parte. `hoje` é injetável (`Callable[[], date]`, padrão `datetime.now(UTC).date()`), o que é o
que torna esse comportamento testável sem mockar relógio de verdade.

### Atividade apagada no Strava — `ActivityRepository.por_start_date_utc` (novo)

`SELECT ... WHERE activity_id IS NOT NULL AND start_date_utc >= ?` — o filtro por
`activity_id IS NOT NULL` exclui linhas manuais de propósito: elas não têm contrapartida no
Strava para "sumir", e apareceriam como falsos positivos em todo corredor adotado.

A comparação usa os ids do `payloads` **completo** (antes do filtro `only_runs`), não só das
corridas: uma atividade retipada (deixou de ser `Run`) não deveria disparar o alarme de
"apagada" — ela só deixou de interessar à pesquisa, o que é uma categoria de evento diferente e
não tratada agora. **Loga, não apaga**: a linha continua no banco e na planilha até alguém
decidir o contrário.

### Erros novos

Nenhum. A fase inteira roda com o vocabulário de exceções que já existia (`utils/errors.py`);
o que mudou foi só o pseudocódigo de `main.py` deixar de ser pseudocódigo.

### Pendências que a Fase 6 deixa para depois

- **Sem jeito de "destravar" um dia preservado por edição humana** — permanece assim desde a
  Fase 5.5; não estava no escopo da Fase 6.
- **`SyncService` não sabe que "hoje" pode ser diferente por fuso** — usa UTC por padrão para
  o limite superior da janela de escrita. Um corredor num fuso muito adiantado poderia ver "o
  dia de hoje" só aparecer no dia seguinte, na pior das hipóteses — auto-corrige na execução
  seguinte, então não foi tratado como bug.
- ~~Fase 7 (Scheduler) decide a cadência real das execuções...~~ — feito, ver a seção
  "Fase 7 (Scheduler) — como ficou" abaixo.

## Fase 7 (Scheduler) — como ficou

### `src/scheduler.py` — o wrapper

O gatilho continua **externo** (Windows Task Scheduler, cron, Docker+cron, GitHub Actions) —
não existe loop nem `sleep` internos, de propósito. `executar()` chama `main.main()` e é só a
rede de segurança para o que sobra do vocabulário de exceções já tratado ali: um bug de
configuração ou de infraestrutura antes do logging subir não pode virar um traceback cru na
tela do agendador. Devolve o código de saída de `main()` sem alteração no caminho normal.

Exemplo de agendamento no Windows (uma execução por dia, às 6h):

```powershell
schtasks /create /tn "StravaSync" /tr "uv run python -m src.scheduler" /sc daily /st 06:00 /sd 06:00
```

Em Linux, o equivalente é uma linha de `crontab -e`: `0 6 * * * cd /caminho/do/projeto && uv run python -m src.scheduler`.

### Schema v4 — falhas consecutivas

`corredor_state` ganha `falhas_consecutivas INTEGER NOT NULL DEFAULT 0` (migração aditiva, como
as três anteriores). `CorredorStateRepository.registrar_falha(corredor_id)` incrementa e devolve
o novo valor (via `RETURNING`, suportado pelo SQLite embutido no Python 3.12);
`registrar_sincronizacao` zera o contador — só é chamado quando a sincronização daquele
corredor terminou com sucesso. `salvar_token` (troca/renovação de OAuth) **não mexe** no
contador: renovar um token não é o mesmo que ter sincronizado com sucesso.

⚠️ **Armadilha descoberta ao migrar o teste de v1**: `test_migracao_de_v1_preserva_o_estado`
simulava "um banco só na v1" fazendo `monkeypatch.setattr(database_service, "SCHEMA_VERSION",
1)` e chamando `init_schema()` — mas os blocos `if versao < N:` comparam contra números
literais, não contra a constante `SCHEMA_VERSION`, então isso sempre aplicou **todas** as
migrações de qualquer forma (só a `PRAGMA user_version` final ficava errada). Isso nunca deu
erro porque `_SCHEMA_V2`/`_SCHEMA_V3` são idempotentes (`CREATE ... IF NOT EXISTS`) — mas
`_SCHEMA_V4` usa `ALTER TABLE ADD COLUMN`, que **não é**, e rodar duas vezes quebra com
"duplicate column name". O teste foi corrigido para semear o banco v1 com SQL cru (só as
colunas que existiam na v1 de verdade), em vez de confiar no monkeypatch.

### `main.py` — ordenação e alerta

`sincronizar_todos` ordena `config.corredores` por `ultima_sincronizacao` crescente antes do
laço (`_ordenar_por_prioridade`) — "nunca sincronizado" conta como o valor mais urgente de
todos. `sorted` é estável, então um banco novo (todos empatados em "nunca") preserva a ordem do
`corredores.toml`. Sem isso, uma `QuotaExhaustedError` sempre deixaria pendente o mesmo grupo no
fim do cadastro.

Toda falha que já contava para `falhas` (isolada por corredor: `RevokedTokenError`,
`AuthorizationError`/`StravaError`, `Exception` genérica) agora também chama
`_registrar_falha_e_alertar`, que grava no banco e solta `logger.critical` quando o total for
**múltiplo** de `config.alerta_falhas_consecutivas` (padrão 3, `ALERTA_FALHAS_CONSECUTIVAS` no
`.env`) — múltiplo, não só "maior ou igual", para não logar `CRITICAL` a cada execução para
sempre depois que o problema já foi visto, mas ainda lembrar periodicamente.
`QuotaExhaustedError` continua **não contando** como falha de ninguém — nem soma ao contador
persistido, só ao contador local da execução.

### Fora de escopo (de propósito)

- Nenhum daemon Python interno nem lib de agendamento nova — o stub já documentava o gatilho
  externo, e isso não mudou.
- Nenhum canal de notificação real (e-mail/Telegram/Discord) — está em "Melhorias Futuras"; o
  alerta desta fase é só um log `CRITICAL`.
- Nenhum limite configurável de "corredores por execução" — a ordenação por recência já resolve
  a injustiça do `TASKS.md` sem precisar de um cursor persistido novo.
