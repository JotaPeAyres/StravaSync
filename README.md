# StravaSync

Aplicação Python que sincroniza automaticamente as corridas do Strava para planilhas Excel de **monitoramento de carga de treino (ACWR / EWMA)**, no contexto de uma **pesquisa com múltiplos corredores**.

A cada execução a aplicação percorre **todos os participantes cadastrados**: consulta a API do Strava, identifica novas corridas, **agrega os dados por dia** e alimenta a planilha daquele corredor — preservando as fórmulas e os gráficos do modelo. Um banco SQLite local guarda os dados ricos de cada atividade (para evitar duplicidades e permitir estatísticas futuras).

Cada participante entra no estudo numa data própria e tem a sua planilha; a falha de um (token expirado, planilha aberta no Excel) é registrada e **não interrompe a coleta dos demais**.

> **Status:** em desenvolvimento. Estrutura em camadas (Fase 1), configuração/logging (Fase 2) e integração com o Strava — OAuth, cliente da API e controle de vazão (Fase 3) — estão prontas. O banco de atividades, o Excel e a orquestração (Fases 4–6) ainda não. Veja o backlog em `TASKS.md`.

---

## O que a aplicação faz

- Autentica no Strava via **OAuth2** e renova o Access Token automaticamente.
- Busca as atividades do atleta e importa **apenas corridas (Run)**, sem duplicar.
- **Agrega as corridas por dia** e escreve na planilha de carga: `Data`, `Carga diária (Km)`, `Pace` e `Tempo total`.
- **Preserva** as fórmulas (EWMA agudo 7d, EWMA crônico 28d, ACWR, PCAC, etc.), constantes e named ranges do template — nunca sobrescreve colunas calculadas.
- Mantém um **SQLite** local com os dados detalhados por atividade (FC, cadência, elevação, ID, tipo…).
- Executa diariamente via **scheduler**, com **logs** de cada etapa.

## A planilha (uma por corredor)

O arquivo base `Cópia de Planilha_carga_corrida.xlsx` é o **template**: cada participante recebe uma cópia própria. Como os corredores entram no estudo em datas diferentes, **cada um tem a sua própria `start_date`**, e portanto uma grade que começa num dia diferente.

### Cadastrar um participante

1. Copie o template para a pasta `data/`, com o identificador do participante:
   ```bash
   cp "Cópia de Planilha_carga_corrida.xlsx" data/p001.xlsx
   ```
2. Acrescente um bloco ao `corredores.toml`:
   ```toml
   [[corredor]]
   id = "p001"
   nome = "Participante 001"
   refresh_token = "cole-aqui-o-token-do-participante"
   start_date = 2026-01-01
   ```
3. Peça a autorização do participante (abaixo). O token real fica no SQLite; o
   valor do TOML é só o de partida.

O `excel_path` é opcional: sem ele, o app usa `./data/<id>.xlsx`. A partir daí os dias são preenchidos de forma **contígua** (dias sem corrida recebem carga `0`), mapeando `dia = (data - start_date).days + 1` → `linha = dia + 1`.

> ⚠️ **`start_date` é um dado do estudo, definido uma vez.** Ela é registrada explicitamente por participante — e não deduzida de quando o script rodou pela primeira vez, porque uma execução atrasada excluiria dias de coleta sem ninguém perceber. O que o corredor correu antes dela **não entra** na planilha.
>
> Ela pode estar no **futuro** (o participante entra na pesquisa semana que vem, o cadastro é feito hoje): enquanto esse dia não chega, não há o que sincronizar.
>
> Depois que a planilha tiver dados, a **célula `B2` é a fonte da verdade**: o app compara com o cadastro e **aborta aquele corredor se divergir**, em vez de reescrever a grade deslocada. Para recomeçar, use uma cópia nova do template.

### Inscrever um participante (autorização no Strava)

A inscrição é **manual**: não há servidor nem página hospedada. O participante
abre um link, aprova, e devolve a URL que ficou na barra de endereço.

1. Gere o link:
   ```bash
   uv run python -m src.inscricao link p001
   ```
2. Envie o link. Ao aprovar, o navegador tenta abrir `http://localhost/...` e a
   **página não carrega** — isso é esperado. Peça a **URL inteira** da barra de
   endereço.
3. Troque pelo token:
   ```bash
   uv run python -m src.inscricao trocar "http://localhost/exchange_token?state=p001&code=...&scope=..."
   ```
4. Confira quem já está inscrito (sai com código 1 se faltar alguém):
   ```bash
   uv run python -m src.inscricao estado
   ```

> ⚠️ **O `code` é de uso único e de vida curta.** Como ele viaja por mensagem
> até você, rode o `trocar` assim que a URL chegar; se expirar, o erro diz para
> gerar um link novo.
>
> **Peça a URL inteira, não só o `code`.** A tela de consentimento do Strava tem
> caixas de seleção: o participante pode aprovar e desmarcar o acesso às
> atividades privadas. O escopo concedido **não vem** na resposta da troca — ele
> aparece só na URL de retorno. Por isso, colar apenas o `code` é recusado (há
> `--sem-verificar-escopo` para forçar, mas aí o problema só apareceria numa
> coleta vazia meses depois).
>
> **Depois da primeira renovação bem-sucedida, editar o `refresh_token` no
> `corredores.toml` não tem efeito nenhum** — o token corrente passa a vir do
> SQLite. Para trocar, reinscreva com o comando `trocar`.

### Adoção de uma planilha já preenchida à mão

Uma planilha que já vinha sendo preenchida manualmente **não** é recomeçada do zero. Nesse caso valem duas datas:

| Campo | Significado |
|-------|-------------|
| `start_date` | O `Dia 1` da planilha — a data que está na célula `B2` |
| `cutover_date` | O primeiro dia em que o app assume a escrita |

Tudo **antes** da data de corte é território do corredor: o app lê para entender o histórico, mas nunca escreve. Da data de corte em diante, o app assume. Para um participante novo as duas datas coincidem (e `cutover_date` pode ser omitida).

O preenchimento manual continua possível depois do corte, para exceções (esteira, treino sem relógio). Como o app registra o que escreveu em cada dia, um valor que ele não reconhece é tratado como edição manual: em vez de sobrescrever, ele preserva e registra no log.

Abas do modelo:

| Aba | Conteúdo | O app escreve |
|-----|----------|---------------|
| `Daily_Data` | 1 linha por dia; carga diária + indicadores ACWR/EWMA | `Data`, `Carga diária (Km)` |
| `Weekly_Desacoplado` | Agregação semanal (100% fórmulas) | — (nada) |
| `Gráficos` | Gráficos de acompanhamento (named ranges dinâmicos) | — (nada) |
| `PACE` | 1 linha por dia; ritmo e tempo | `Data`, `Carga diária (Km)`, `PACE`, `Tempo total de treinamento` |

Detalhes das colunas (entrada vs. fórmula) em `PROJECT_SCOPE.md` e no [CLAUDE.md](CLAUDE.md).

## Tecnologias

Python 3.12+, `httpx`, `openpyxl`, `pandas`, SQLite, `python-dotenv`, `logging`, `pytest`. Gerenciador de pacotes: **uv**.

## Configuração

São **dois arquivos**, porque os dados têm donos diferentes — e ambos ficam fora do versionamento, por conterem segredos:

```bash
cp .env.example .env
```

```bash
cp corredores.example.toml corredores.toml
```

### `.env` — o que pertence à aplicação

As credenciais são do **app registrado no Strava** para a pesquisa inteira, e valem para todos os participantes.

| Variável | Obrigatória | Descrição |
|----------|:-----------:|-----------|
| `STRAVA_CLIENT_ID` | sim | Credenciais da aplicação — [strava.com/settings/api](https://www.strava.com/settings/api) |
| `STRAVA_CLIENT_SECRET` | sim | |
| `CORREDORES_PATH` | não | Cadastro dos participantes (padrão `./corredores.toml`) |
| `TEMPLATE_PATH` | não | Modelo base (padrão: a planilha versionada na raiz do repo) |
| `DATABASE_PATH` | não | Banco SQLite (padrão `./data/stravasync.db`) |
| `LOG_LEVEL` | não | `DEBUG`…`CRITICAL` (padrão `INFO`) |
| `LOG_FILE` | não | Log rotativo (padrão `./data/stravasync.log`); vazio = só console |
| `STRAVA_TIMEOUT_S` | não | Timeout de cada requisição, em segundos (padrão `20`, mínimo `1`) |
| `STRAVA_PAUSA_ENTRE_CHAMADAS_S` | não | Pausa mínima entre chamadas (padrão `1`) — o botão para desacelerar quando a pesquisa cresce |
| `STRAVA_RESERVA_DE_VAZAO` | não | Folga guardada na janela de 15 min (padrão `10`), para uma inscrição manual não esbarrar no teto |

### `corredores.toml` — o que pertence a cada participante

Um bloco `[[corredor]]` por pessoa.

| Campo | Obrigatório | Descrição |
|-------|:-----------:|-----------|
| `id` | sim | Identificador na pesquisa; precisa ser único |
| `refresh_token` | sim | Token **inicial**, de quando o participante autorizou o app |
| `start_date` | sim | Entrada na pesquisa = `Dia 1` da planilha |
| `nome` | não | Só para o log (padrão: o `id`) |
| `cutover_date` | não | Primeiro dia em que o app escreve (padrão: `start_date`) |
| `excel_path` | não | Planilha do participante (padrão `./data/<id>.xlsx`); única por corredor |

O `refresh_token` do arquivo é apenas o de partida: o token corrente vive no SQLite, porque o Strava pode devolver um token novo a cada renovação e **o app não reescreve este arquivo** — isso destruiria comentários e formatação.

Caminhos relativos são resolvidos a partir da **raiz do projeto**, não do diretório de execução — o scheduler pode rodar de qualquer lugar. Variáveis já definidas no ambiente têm precedência sobre o `.env`, o que permite sobrescrever a configuração em CI ou no agendador.

Se algo estiver faltando ou inválido, o app aborta na inicialização listando **todas** as pendências de uma vez, identificando o corredor em cada uma — com dezenas de participantes, corrigir um erro por execução seria inviável.

> ⚠️ **Limite de requisições.** O teto do Strava é **por aplicação** (padrão: 100 requisições/15 min, 1000/dia) e é dividido por todos os participantes da pesquisa. O app espaça as chamadas, lê o uso corrente nos cabeçalhos de cada resposta e respeita o `429`. Ao bater a janela de 15 minutos ele **espera** a virada; ao esgotar a cota diária ele **encerra** a execução, deixando os corredores restantes para a próxima — dormir horas num job agendado seria pior. Ajuste `STRAVA_PAUSA_ENTRE_CHAMADAS_S` quando a pesquisa crescer.
>
> Registre **um único app** no Strava para o estudo inteiro, com *Authorization Callback Domain* = `localhost`. Um segundo `client_id` invalidaria de uma vez os tokens de todos os participantes.

## Como rodar

```bash
uv sync
```

```bash
uv run python -m src.main
```

Testes e lint:

```bash
uv run pytest
```

```bash
uv run ruff check .
```

## Documentação

Os documentos de planejamento são mantidos **apenas localmente** (estão no `.gitignore`), na raiz do projeto:

- `PROJECT_SCOPE.md` — objetivos, dados armazenados e modelo do Excel.
- `ARCHITECTURE.md` — arquitetura em camadas e responsabilidades.
- `TASKS.md` — backlog por fases.

O [CLAUDE.md](CLAUDE.md), versionado, resume o contexto essencial do projeto.
