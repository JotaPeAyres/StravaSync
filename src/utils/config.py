"""Carrega a configuração da pesquisa a partir do ambiente e do cadastro.

São duas camadas, porque os dados têm donos diferentes:

- **`.env`** — o que pertence à *aplicação*: as credenciais do app registrado no
  Strava (`CLIENT_ID`/`CLIENT_SECRET`, compartilhadas por todos os participantes),
  os caminhos do template e do banco, e o logging.
- **`corredores.toml`** — o que pertence a *cada participante*: `refresh_token`
  inicial, data de entrada na pesquisa, data de corte e a planilha dele.

O `refresh_token` do arquivo é apenas o de partida (bootstrap). O token corrente
é estado mutável e vive no SQLite: o Strava pode devolver um token novo a cada
renovação, e reescrever um arquivo editado à mão destruiria comentários e
formatação.

Se algo estiver faltando ou mal formatado, `load_config()` levanta `ConfigError`
listando **todos** os problemas de uma vez — com dezenas de participantes,
corrigir um erro por execução seria inviável.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.models.corredor import Corredor
from src.utils.errors import StravaSyncError

# Raiz do repositório (src/utils/config.py -> src/utils -> src -> raiz).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Caminhos relativos são resolvidos a partir da raiz do projeto, e não do
# diretório de trabalho — o scheduler (Fase 7) pode executar de qualquer lugar.
DEFAULT_CORREDORES_PATH = "./corredores.toml"
DEFAULT_TEMPLATE_PATH = "./Cópia de Planilha_carga_corrida.xlsx"
DEFAULT_DATABASE_PATH = "./data/stravasync.db"
DEFAULT_LOG_FILE = "./data/stravasync.log"
DEFAULT_LOG_LEVEL = "INFO"

# Parâmetros de vazão. O limite do Strava é por APLICAÇÃO (100 req/15min,
# 1000/dia) e é dividido por todos os participantes — com a pesquisa crescendo,
# o operador precisa poder desacelerar sem esperar uma nova versão do código.
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_PAUSA_S = 1.0
DEFAULT_RESERVA = 10

# Um timeout muito curto transforma rede lenta em falha do participante.
TIMEOUT_MINIMO_S = 1.0
# A reserva é o quanto da janela de 15 min fica intocado; acima disso não
# sobraria cota nenhuma para a execução.
RESERVA_MAXIMA = 90

_NIVEIS_DE_LOG = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}

_CAMPOS_CONHECIDOS = {
    "id",
    "nome",
    "refresh_token",
    "start_date",
    "cutover_date",
    "excel_path",
}


class ConfigError(StravaSyncError):
    """Configuração ausente ou inválida — o app não tem como seguir.

    `StravaSyncError` herda de `RuntimeError`, então quem já capturava
    `RuntimeError` continua funcionando.
    """


@dataclass(frozen=True)
class Config:
    """Configuração da pesquisa: credenciais da aplicação + participantes."""

    # Credenciais do app registrado no Strava, comuns a todos os participantes.
    # Fora do repr para não vazarem em log ou traceback.
    strava_client_id: str = field(repr=False)
    strava_client_secret: str = field(repr=False)

    template_path: Path
    database_path: Path
    corredores: tuple[Corredor, ...]

    log_level: str = DEFAULT_LOG_LEVEL
    log_file: Path | None = None

    # Vazão: campos novos entram sempre no fim, senão o dataclass não compila
    # (campo com padrão não pode preceder campo sem padrão).
    strava_timeout_s: float = DEFAULT_TIMEOUT_S
    strava_pausa_entre_chamadas_s: float = DEFAULT_PAUSA_S
    strava_reserva_de_vazao: int = DEFAULT_RESERVA

    def safe_summary(self) -> str:
        """Resumo de uma linha para o log de inicialização, com segredos mascarados."""
        return (
            f"client_id={mascarar(self.strava_client_id)} "
            f"corredores={len(self.corredores)} "
            f"template={self.template_path} "
            f"banco={self.database_path} "
            f"log_level={self.log_level} "
            f"log_file={self.log_file or '(desativado)'} "
            f"timeout={self.strava_timeout_s}s "
            f"pausa={self.strava_pausa_entre_chamadas_s}s "
            f"reserva={self.strava_reserva_de_vazao}"
        )


def load_config(env_file: Path | None = None, corredores_file: Path | None = None) -> Config:
    """Lê o `.env` e o cadastro de corredores, devolvendo um `Config` validado.

    Variáveis já presentes no ambiente têm precedência sobre o `.env`, o que
    permite sobrescrever a configuração no scheduler ou em CI sem editar arquivo.

    Raises:
        ConfigError: se faltar valor obrigatório ou algo for inválido.
    """
    load_dotenv(env_file if env_file is not None else PROJECT_ROOT / ".env")

    erros: list[str] = []

    client_id = _obrigatorio("STRAVA_CLIENT_ID", erros)
    client_secret = _obrigatorio("STRAVA_CLIENT_SECRET", erros)
    log_level = _nivel_de_log("LOG_LEVEL", erros)

    template_path = _caminho(os.getenv("TEMPLATE_PATH") or DEFAULT_TEMPLATE_PATH)
    database_path = _caminho(os.getenv("DATABASE_PATH") or DEFAULT_DATABASE_PATH)

    # LOG_FILE vazio (`LOG_FILE=`) desliga o arquivo de log, mantendo só o console.
    log_file_bruto = os.getenv("LOG_FILE", DEFAULT_LOG_FILE).strip()
    log_file = _caminho(log_file_bruto) if log_file_bruto else None

    timeout_s = _numero("STRAVA_TIMEOUT_S", DEFAULT_TIMEOUT_S, erros, minimo=TIMEOUT_MINIMO_S)
    pausa_s = _numero("STRAVA_PAUSA_ENTRE_CHAMADAS_S", DEFAULT_PAUSA_S, erros, minimo=0.0)
    reserva = _numero(
        "STRAVA_RESERVA_DE_VAZAO",
        DEFAULT_RESERVA,
        erros,
        minimo=0.0,
        maximo=RESERVA_MAXIMA,
        inteiro=True,
    )

    cadastro = corredores_file or _caminho(
        os.getenv("CORREDORES_PATH") or DEFAULT_CORREDORES_PATH
    )
    corredores = _carregar_corredores(cadastro, erros)

    if erros:
        raise ConfigError(
            "Configuração inválida:\n  - "
            + "\n  - ".join(erros)
            + "\n\nVeja `.env.example` e `corredores.example.toml`."
        )

    return Config(
        strava_client_id=client_id,
        strava_client_secret=client_secret,
        template_path=template_path,
        database_path=database_path,
        corredores=corredores,
        log_level=log_level,
        log_file=log_file,
        strava_timeout_s=timeout_s,
        strava_pausa_entre_chamadas_s=pausa_s,
        strava_reserva_de_vazao=int(reserva),
    )


def _carregar_corredores(caminho: Path, erros: list[str]) -> tuple[Corredor, ...]:
    """Lê o cadastro TOML e valida cada participante, acumulando os erros."""
    if not caminho.is_file():
        erros.append(
            f"cadastro de corredores não encontrado em {caminho} "
            "(copie `corredores.example.toml`)"
        )
        return ()

    try:
        with caminho.open("rb") as arquivo:
            dados = tomllib.load(arquivo)
    except tomllib.TOMLDecodeError as erro:
        erros.append(f"{caminho} não é um TOML válido: {erro}")
        return ()

    entradas = dados.get("corredor")
    if not entradas:
        erros.append(f"{caminho} não tem nenhum corredor cadastrado (bloco [[corredor]])")
        return ()

    corredores: list[Corredor] = []
    for posicao, entrada in enumerate(entradas, start=1):
        corredor = _montar_corredor(entrada, posicao, erros)
        if corredor is not None:
            corredores.append(corredor)

    _validar_unicidade(corredores, erros)
    return tuple(corredores)


def _montar_corredor(entrada: dict, posicao: int, erros: list[str]) -> Corredor | None:
    """Valida um bloco `[[corredor]]`; devolve None se ele estiver inutilizável."""
    identificador = str(entrada.get("id") or "").strip()
    rotulo = f"corredor[{posicao}]" if not identificador else f"corredor {identificador!r}"

    if not identificador:
        erros.append(f"{rotulo}: `id` é obrigatório")
        return None

    if desconhecidos := set(entrada) - _CAMPOS_CONHECIDOS:
        # Um campo com nome errado seria ignorado em silêncio — e uma data de
        # entrada digitada como `data_inicio` viraria participante sem histórico.
        erros.append(f"{rotulo}: campo(s) não reconhecido(s): {', '.join(sorted(desconhecidos))}")

    token = str(entrada.get("refresh_token") or "").strip()
    if not token:
        erros.append(f"{rotulo}: `refresh_token` é obrigatório")

    start_date = _data_do_cadastro(entrada, "start_date", rotulo, erros)
    if start_date is None:
        return None

    cutover_date = _data_do_cadastro(entrada, "cutover_date", rotulo, erros) or start_date
    if cutover_date < start_date:
        erros.append(
            f"{rotulo}: cutover_date={cutover_date.isoformat()} é anterior a "
            f"start_date={start_date.isoformat()} (o corte não pode ficar fora da planilha)"
        )

    excel_bruto = str(entrada.get("excel_path") or f"./data/{identificador}.xlsx")

    if not token:
        return None

    return Corredor(
        id=identificador,
        nome=str(entrada.get("nome") or identificador).strip(),
        refresh_token=token,
        start_date=start_date,
        cutover_date=cutover_date,
        excel_path=_caminho(excel_bruto),
    )


def _data_do_cadastro(
    entrada: dict, campo: str, rotulo: str, erros: list[str]
) -> date | None:
    """Lê uma data do TOML, aceitando data nativa ou string YYYY-MM-DD."""
    valor = entrada.get(campo)
    if valor is None:
        if campo == "start_date":
            erros.append(f"{rotulo}: `start_date` é obrigatória (entrada do corredor na pesquisa)")
        return None

    if isinstance(valor, date):
        return valor

    try:
        return date.fromisoformat(str(valor).strip())
    except ValueError:
        erros.append(f"{rotulo}: {campo}={valor!r} não é uma data no formato YYYY-MM-DD")
        return None


def _validar_unicidade(corredores: list[Corredor], erros: list[str]) -> None:
    """Ids e planilhas repetidos corrompem dados — um corredor sobrescreveria o outro."""
    for campo, valores in (
        ("id", [c.id for c in corredores]),
        ("excel_path", [str(c.excel_path) for c in corredores]),
    ):
        vistos: set[str] = set()
        for valor in valores:
            if valor in vistos:
                erros.append(f"{campo} repetido no cadastro: {valor!r}")
            vistos.add(valor)


def _obrigatorio(nome: str, erros: list[str]) -> str:
    """Devolve a variável de ambiente `nome`, acumulando erro se estiver vazia."""
    valor = (os.getenv(nome) or "").strip()
    if not valor:
        erros.append(f"{nome} não definida")
    return valor


def _nivel_de_log(nome: str, erros: list[str]) -> str:
    """Valida o nível de log, caindo no padrão quando a variável não é definida."""
    valor = (os.getenv(nome) or DEFAULT_LOG_LEVEL).strip().upper()
    if valor not in _NIVEIS_DE_LOG:
        erros.append(f"{nome}={valor!r} inválido (use um de: {', '.join(sorted(_NIVEIS_DE_LOG))})")
        return DEFAULT_LOG_LEVEL
    return valor


def _numero(
    nome: str,
    padrao: float,
    erros: list[str],
    *,
    minimo: float,
    maximo: float | None = None,
    inteiro: bool = False,
) -> float:
    """Lê um número do ambiente, acumulando erro em vez de levantar na hora.

    Segue o padrão do `_nivel_de_log`: quando o valor é inválido, devolve o
    padrão para que a validação continue e o usuário veja todos os problemas de
    uma vez.
    """
    bruto = (os.getenv(nome) or "").strip()
    if not bruto:
        return padrao

    try:
        valor = float(int(bruto) if inteiro else float(bruto))
    except ValueError:
        tipo = "um número inteiro" if inteiro else "um número"
        erros.append(f"{nome}={bruto!r} não é {tipo}")
        return padrao

    if valor < minimo or (maximo is not None and valor > maximo):
        faixa = f"maior ou igual a {minimo}" if maximo is None else f"entre {minimo} e {maximo}"
        erros.append(f"{nome}={bruto!r} fora da faixa aceita ({faixa})")
        return padrao

    return valor


def _caminho(valor: str) -> Path:
    """Resolve um caminho de configuração; relativos partem da raiz do projeto."""
    caminho = Path(valor.strip()).expanduser()
    if not caminho.is_absolute():
        caminho = PROJECT_ROOT / caminho
    return Path(os.path.normpath(caminho))


def mascarar(segredo: str) -> str:
    """Mostra apenas os últimos 4 caracteres de um segredo.

    Público porque o CLI de inscrição e os logs de token precisam do mesmo
    tratamento — duas implementações divergentes vazariam mais cedo ou mais tarde.
    """
    if not segredo or len(segredo) <= 4:
        return "****"
    return f"****{segredo[-4:]}"
