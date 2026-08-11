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

O arquivo base `Cópia de Planilha_carga_corrida.xlsx` é o **template**. O app é **single-athlete**: uma instância = um corredor = uma planilha.

### Onboarding de um corredor (passo manual)

1. Copie o template para a pasta `data/`, com um nome próprio:
   ```bash
   cp "Cópia de Planilha_carga_corrida.xlsx" data/corredor.xlsx
   ```
2. Aponte `EXCEL_PATH` no `.env` para essa cópia (e `TEMPLATE_PATH` para o modelo base).
3. Defina `START_DATE` — a data que corresponde ao **`Dia 1`** (linha 2) da planilha.
4. Preencha as credenciais do Strava (`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN`).

A partir daí o app preenche os dias de forma **contígua** (dias sem corrida recebem carga `0`), mapeando `dia = (data - START_DATE).days + 1` → `linha = dia + 1`.

> ⚠️ **`START_DATE` é definida uma vez.** Alterá-la depois desloca o mapeamento data → linha e desalinha tudo que já foi gravado. Para recomeçar, use uma cópia nova do template.

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
| `START_DATE` | sim | Data do `Dia 1` da planilha (`YYYY-MM-DD`) |
| `EXCEL_PATH` | não | Planilha do corredor (padrão `./data/corredor.xlsx`) |
| `TEMPLATE_PATH` | não | Modelo base (padrão `./data/template.xlsx`) |
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
