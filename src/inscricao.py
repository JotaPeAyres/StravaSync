"""Ferramenta de operador para inscrever participantes na pesquisa.

Fluxo, para cada participante:

1. `python -m src.inscricao link p001` — gera o link e você envia à pessoa.
2. A pessoa abre, aprova, e a página **não carrega** (é `localhost`). Ela copia a
   URL da barra de endereço e devolve.
3. `python -m src.inscricao trocar "<url colada>"` — trocamos o `code` por
   tokens e gravamos no banco. O `state` da URL diz de quem ela é, então não é
   preciso informar o corredor.
4. `python -m src.inscricao estado` — conferência: quem já autorizou, com qual
   conta do Strava, com qual escopo e até quando a coleta chegou.

Fica separado do `main.py` de propósito: aquele é a rotina que o agendador roda
sem ninguém olhando; este é o que uma pessoa executa, com argumentos e saída na
tela.

⚠️ O `code` é de uso único e de vida curta — rode o `trocar` assim que a URL
chegar. Se demorar, o erro diz para pedir um link novo.
"""
from __future__ import annotations

import argparse
import sys

from src.api.rate_limiter import RateLimiter
from src.api.strava_client import StravaClient
from src.models.corredor import Corredor
from src.repositories.corredor_state_repository import CorredorStateRepository
from src.services.database_service import DatabaseService
from src.utils.auth import inscrever, ler_retorno, url_de_autorizacao
from src.utils.config import Config, ConfigError, load_config, mascarar
from src.utils.errors import StravaSyncError
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Executa o subcomando pedido. Devolve o código de saída do processo."""
    args = _parser().parse_args(argv)

    try:
        config = load_config()
    except ConfigError as erro:
        print(f"[StravaSync] {erro}", file=sys.stderr)
        return 1

    setup_logging(level=config.log_level, log_file=config.log_file)

    try:
        return args.executar(config, args)
    except StravaSyncError as erro:
        # Erro tratado: o operador precisa da mensagem, não de um traceback.
        print(f"[StravaSync] {erro}", file=sys.stderr)
        return 1


def comando_link(config: Config, args: argparse.Namespace) -> int:
    """Imprime o link de autorização de um corredor."""
    corredor = _corredor(config, args.corredor)
    print(url_de_autorizacao(config.strava_client_id, corredor.id))
    print()
    print(f"Envie o link acima para {corredor.nome} ({corredor.id}).")
    print("Depois de aprovar, a página não vai carregar — isso é esperado.")
    print("Peça a URL INTEIRA da barra de endereço: é dela que sai o escopo concedido.")
    print('Ao receber:  python -m src.inscricao trocar "<url colada>"')
    return 0


def comando_trocar(config: Config, args: argparse.Namespace) -> int:
    """Troca o `code` recebido por tokens e grava o estado do participante."""
    retorno = ler_retorno(args.retorno)
    corredor = _identificar(config, retorno.state, args.corredor)

    with DatabaseService(config.database_path) as banco:
        repo = CorredorStateRepository(banco.connect())
        with _cliente(config) as client:
            estado = inscrever(
                corredor,
                retorno,
                client=client,
                repo=repo,
                permitir_nao_verificado=args.sem_verificar_escopo,
                forcar_atleta=args.forcar_atleta,
            )

    print(f"{corredor} inscrito com sucesso.")
    print(f"  conta Strava: {estado.athlete_id if estado.athlete_id else '(não informada)'}")
    print(f"  escopo:       {estado.scope or '(NÃO VERIFICADO)'}")
    print(f"  planilha:     {corredor.excel_path.name}")
    print(f"  dia 1:        {corredor.start_date.isoformat()}")
    return 0


def comando_estado(config: Config, args: argparse.Namespace) -> int:
    """Mostra o que está gravado no banco para cada participante."""
    with DatabaseService(config.database_path) as banco:
        repo = CorredorStateRepository(banco.connect())
        estados = {estado.corredor_id: estado for estado in repo.listar()}

    corredores = config.corredores
    if args.corredor:
        corredores = (_corredor(config, args.corredor),)

    pendentes = 0
    for corredor in corredores:
        estado = estados.get(corredor.id)
        if estado is None:
            print(f"{corredor.id:<10} não inscrito — rode: python -m src.inscricao link {corredor.id}")
            pendentes += 1
            continue

        marca = "PRECISA REINSCREVER" if estado.precisa_reinscricao else "ok"
        if estado.precisa_reinscricao:
            pendentes += 1

        print(
            f"{corredor.id:<10} {marca:<20} "
            f"atleta={estado.athlete_id or '?'} "
            f"escopo={estado.scope or '(não verificado)'} "
            f"token={mascarar(estado.refresh_token)} "
            f"última sync={_data(estado.ultima_sincronizacao)}"
        )
        if estado.motivo_reinscricao:
            print(f"{'':<10} motivo: {estado.motivo_reinscricao}")

    # Sai com 1 se alguém está pendente: assim dá para usar num check automático.
    return 1 if pendentes else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.inscricao",
        description="Inscreve participantes da pesquisa via OAuth do Strava.",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_link = sub.add_parser("link", help="gera o link de autorização de um corredor")
    p_link.add_argument("corredor", help="id do corredor no corredores.toml")
    p_link.set_defaults(executar=comando_link)

    p_trocar = sub.add_parser("trocar", help="troca o code (ou a URL colada) por tokens")
    p_trocar.add_argument("retorno", help="a URL inteira que o participante devolveu, ou o code")
    p_trocar.add_argument(
        "--corredor",
        help="id do corredor; obrigatório apenas quando a URL não traz `state`",
    )
    p_trocar.add_argument(
        "--sem-verificar-escopo",
        action="store_true",
        help=(
            "aceita a troca sem saber o escopo concedido (só quando o participante "
            "colou apenas o code) — por sua conta e risco"
        ),
    )
    p_trocar.add_argument(
        "--forcar-atleta",
        action="store_true",
        help="aceita que a conta do Strava mudou em relação à inscrição anterior",
    )
    p_trocar.set_defaults(executar=comando_trocar)

    p_estado = sub.add_parser("estado", help="mostra o estado gravado dos participantes")
    p_estado.add_argument("corredor", nargs="?", help="id do corredor (padrão: todos)")
    p_estado.set_defaults(executar=comando_estado)

    return parser


def _cliente(config: Config) -> StravaClient:
    """Cliente com a mesma vazão da execução normal.

    A inscrição divide a cota com a coleta — é a mesma aplicação no Strava.
    """
    limitador = RateLimiter(
        pausa_s=config.strava_pausa_entre_chamadas_s,
        reserva=config.strava_reserva_de_vazao,
    )
    return StravaClient(
        config.strava_client_id,
        config.strava_client_secret,
        limiter=limitador,
        timeout_s=config.strava_timeout_s,
    )


def _corredor(config: Config, identificador: str) -> Corredor:
    """Busca o corredor no cadastro, com erro útil quando o id não existe."""
    for corredor in config.corredores:
        if corredor.id == identificador:
            return corredor

    conhecidos = ", ".join(c.id for c in config.corredores) or "(cadastro vazio)"
    raise ConfigError(
        f"corredor {identificador!r} não está no cadastro. Conhecidos: {conhecidos}. "
        "Acrescente um bloco [[corredor]] ao corredores.toml antes de inscrever."
    )


def _identificar(config: Config, state: str | None, informado: str | None) -> Corredor:
    """Decide de quem é a URL colada.

    O `state` volta intacto do Strava, então a URL se auto-identifica. Quando o
    operador também informa o corredor e os dois divergem, **recusamos**: com
    50+ participantes, gravar o token de um no cadastro de outro é o erro mais
    caro possível, e não é reversível sem procurar as duas pessoas.
    """
    if state and informado and state != informado:
        raise ConfigError(
            f"a URL colada é do corredor {state!r}, mas você informou {informado!r}. "
            "Confira de quem é a URL antes de repetir o comando."
        )

    identificador = state or informado
    if not identificador:
        raise ConfigError(
            "não dá para saber de quem é este código: a URL colada não tem `state`. "
            "Informe o corredor com --corredor <id>, ou peça a URL inteira."
        )

    return _corredor(config, identificador)


def _data(momento) -> str:
    return momento.strftime("%Y-%m-%d %H:%M") if momento else "nunca"


if __name__ == "__main__":
    sys.exit(main())
