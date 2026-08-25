# CLAUDE.md

Contexto do projeto para o Claude Code. **Leia isto antes de agir.** Documento vivo — atualize conforme o projeto avança.

## O que é

**StravaSync**: app Python que sincroniza corridas do Strava para planilhas Excel de **carga de treino (ACWR / EWMA)**, no contexto de uma **pesquisa com múltiplos corredores**. A cada execução percorre **todos os participantes cadastrados**: agrega as corridas de cada um **por dia**, alimenta a planilha dele **preservando fórmulas/gráficos**, e mantém um SQLite com os dados ricos por atividade (dedupe + estatísticas futuras).

## Estado atual

- **Fase 1 (Estrutura): concluída e na `main`** (branch `fase-1-estrutura` mergeada; pode ser apagada).
- **Fase 2 (Configuração): concluída e na `main`** — config em duas camadas (`.env` global + `corredores.toml`), logging central, modelos (`Activity`, `DailyLoad`, `Corredor`), 54 testes. Branch `fase-2-configuracao` mergeada; pode ser apagada.
- **Fase 3 (Strava): implementada na branch `fase-3-strava`**, ainda **não mergeada**. Entrega: hierarquia de exceções, tabela de estado por corredor no SQLite, `RateLimiter`, `StravaClient`, política OAuth (`utils/auth.py`), filtro/conversão de atividades e a CLI `src/inscricao.py`. 197 testes, tudo com `httpx.MockTransport` — **nenhuma credencial real foi usada**.
- **Fase 4 (Banco): implementada na branch `fase-4-banco`** (criada a partir de `fase-3-strava`, já que a Fase 3 não foi mergeada). Entrega: schema v2 com `activities`, `ActivityRepository` com upsert, consultas por período e a marca d'água do `after=`. 238 testes.
- A `main` local está **1 merge à frente do `origin/main`** — nada foi enviado ainda.
- **Pendência bloqueante para validar as Fases 3 e 4 de verdade**: registrar o app no Strava (*Authorization Callback Domain* = `localhost`) e preencher `STRAVA_CLIENT_ID`/`STRAVA_CLIENT_SECRET` no `.env`, hoje vazios. Não bloqueia o desenvolvimento — a Fase 5 (Excel) também é local.
- **Próxima: Fase 5 (Excel)** — `ExcelService` com openpyxl, escrevendo só as células de entrada.

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
  services/activity_service.py         # ✅ Fase 3 (agregação: Fase 6)
  services/sync_service.py             # Fase 6 (orquestra tudo)
  services/excel_service.py            # Fase 5 (escreve entradas, preserva fórmulas)
  services/database_service.py         # ✅ Fase 3 (schema v1) — v2 na Fase 4
  models/activity.py, models/daily_load.py, models/corredor.py ✅
  repositories/activity_repository.py  # ✅ Fase 4
  repositories/corredor_state_repository.py  # ✅ Fase 3
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

O `after=` filtra por `start_date` em **UTC**, não pelo horário local. E usar o relógio da última execução perderia **para sempre** uma corrida de domingo enviada na terça — daí as duas colunas separadas: `ultima_sincronizacao` (quando rodamos) e `ultimo_evento_em` (o `start_date` UTC mais recente visto, que só avança). A fórmula da Fase 6 é `max(meia-noite UTC de cutover_date, ultimo_evento_em − 2 dias)`; **a data-base é `cutover_date`, não `start_date`**.

### Consumo das exceções no `main.py` — especificado, implementação na Fase 6

```
cria UMA vez: DatabaseService, RateLimiter, StravaClient
por corredor: marcado precisa_reinscricao? → WARNING e pula (0 requisições)
  except QuotaExhaustedError:  loga os pendentes e BREAK   # não é falha de corredor
  except RevokedTokenError:    marca reinscrição; falhas += 1; continua
  except (AuthorizationError, StravaError): falhas += 1; continua
```

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
  acomoda desde já o histórico manual da Fase 5.5, evitando migrar depois uma
  tabela cheia de dados de pesquisa. ⚠️ O `ON CONFLICT` **não dispara** com
  `activity_id NULL`: reimportar histórico manual duplicaria. Há um
  `TODO(Fase 5.5)` no `database_service.py` pedindo o índice parcial em v3.
- **Sem FOREIGN KEY** para `corredor_state`: aquilo é cache do OAuth, não
  cadastro (quem existe na pesquisa é o `corredores.toml`). A Fase 5.5 precisa
  adotar planilha de quem ainda não autorizou; uma FK viraria erro.
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

### Pendências que a Fase 4 empurra para a Fase 6

- Agregar a partir do **banco** (`por_dia`), não do retorno da API.
- Reescrever também os `dias_afetados`.
- **Atividade apagada no Strava não é detectada**: ela só para de aparecer, e a
  carga fantasma fica na planilha e no ACWR. A reconciliação é barata (tudo com
  `start_date_utc >= after` deveria ter voltado) e o comportamento deve ser
  **logar, não apagar**.
