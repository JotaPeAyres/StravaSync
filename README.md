# StravaSync

Aplicação Python **single-athlete** que sincroniza automaticamente as corridas do Strava para uma planilha Excel de **monitoramento de carga de treino (ACWR / EWMA)**.

A cada execução a aplicação consulta a API do Strava, identifica novas corridas, **agrega os dados por dia** e alimenta a planilha de carga do corredor — preservando as fórmulas e os gráficos do modelo. Um banco SQLite local guarda os dados ricos de cada atividade (para evitar duplicidades e permitir estatísticas futuras).

> **Status:** em desenvolvimento. A estrutura em camadas (Fase 1) e a configuração/logging (Fase 2) estão prontas; a integração com o Strava, o banco e o Excel (Fases 3–6) ainda não. Veja o backlog em `TASKS.md`.

---

## O que a aplicação faz

- Autentica no Strava via **OAuth2** e renova o Access Token automaticamente.
- Busca as atividades do atleta e importa **apenas corridas (Run)**, sem duplicar.
- **Agrega as corridas por dia** e escreve na planilha de carga: `Data`, `Carga diária (Km)`, `Pace` e `Tempo total`.
- **Preserva** as fórmulas (EWMA agudo 7d, EWMA crônico 28d, ACWR, PCAC, etc.), constantes e named ranges do template — nunca sobrescreve colunas calculadas.
- Mantém um **SQLite** local com os dados detalhados por atividade (FC, cadência, elevação, ID, tipo…).
- Executa diariamente via **scheduler**, com **logs** de cada etapa.

## A planilha (uma por corredor)

O arquivo base `Cópia de Planilha_carga_corrida.xlsx` é o **template**. O app é **single-athlete**: uma instância = um corredor = uma planilha. Como os participantes da pesquisa entram em datas diferentes, **cada corredor tem a sua própria `START_DATE`**.

### Onboarding de um corredor (passo manual)

1. Copie o template para a pasta `data/`, com um nome próprio:
   ```bash
   cp "Cópia de Planilha_carga_corrida.xlsx" data/corredor.xlsx
   ```
2. Aponte `EXCEL_PATH` no `.env` para essa cópia.
3. Defina `START_DATE` — a **data de entrada desse corredor na pesquisa**, ou seja, o primeiro dia em que o projeto passa a buscar dados dele. É o **`Dia 1`** (linha 2) da planilha.
4. Preencha as credenciais do Strava (`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN`).

A partir daí o app preenche os dias de forma **contígua** (dias sem corrida recebem carga `0`), mapeando `dia = (data - START_DATE).days + 1` → `linha = dia + 1`.

> ⚠️ **`START_DATE` é um dado do estudo, definido uma vez.** Ela é registrada explicitamente por corredor — e não deduzida de quando o script rodou pela primeira vez, porque uma execução atrasada excluiria dias de coleta sem ninguém perceber. O que o corredor correu antes dela **não entra** na planilha.
>
> Ela pode estar no **futuro** (o corredor entra na pesquisa semana que vem, a config é feita hoje): enquanto esse dia não chega, não há o que sincronizar.
>
> Depois que a planilha tiver dados, a **célula `B2` é a fonte da verdade**: o app compara com o `.env` e **aborta se divergir**, em vez de reescrever a grade deslocada. Para recomeçar, use uma cópia nova do template.

### Adoção de uma planilha já preenchida à mão

Uma planilha que já vinha sendo preenchida manualmente **não** é recomeçada do zero. Nesse caso valem duas datas:

| Data | Significado |
|------|-------------|
| `START_DATE` | O `Dia 1` da planilha — a data que está na célula `B2` |
| `CUTOVER_DATE` | O primeiro dia em que o app assume a escrita |

Tudo **antes** da data de corte é território do corredor: o app lê para entender o histórico, mas nunca escreve. Da data de corte em diante, o app assume. Para um corredor novo as duas datas coincidem (e `CUTOVER_DATE` pode ficar vazia).

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

Copie `.env.example` para `.env` e preencha:

```bash
cp .env.example .env
```

| Variável | Obrigatória | Descrição |
|----------|:-----------:|-----------|
| `STRAVA_CLIENT_ID` | sim | Credenciais da aplicação — [strava.com/settings/api](https://www.strava.com/settings/api) |
| `STRAVA_CLIENT_SECRET` | sim | |
| `STRAVA_REFRESH_TOKEN` | sim | |
| `START_DATE` | sim | Entrada do corredor na pesquisa = `Dia 1` da planilha (`YYYY-MM-DD`) |
| `CUTOVER_DATE` | não | Primeiro dia em que o app escreve (padrão: `START_DATE`). Ver *Adoção* abaixo |
| `EXCEL_PATH` | não | Planilha do corredor (padrão `./data/corredor.xlsx`) |
| `TEMPLATE_PATH` | não | Modelo base (padrão: a planilha versionada na raiz do repo) |
| `LOG_LEVEL` | não | `DEBUG`…`CRITICAL` (padrão `INFO`) |
| `LOG_FILE` | não | Log rotativo (padrão `./data/stravasync.log`); vazio = só console |

Caminhos relativos são resolvidos a partir da **raiz do projeto**, não do diretório de execução — o scheduler pode rodar de qualquer lugar. Variáveis já definidas no ambiente têm precedência sobre o `.env`, o que permite sobrescrever a configuração em CI ou no agendador.

Se faltar alguma variável obrigatória, o app aborta na inicialização listando **todas** as pendências de uma vez.

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
