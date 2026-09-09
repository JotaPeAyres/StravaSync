"""Ponto de entrada da aplicação StravaSync.

A cada execução, percorre **todos** os participantes da pesquisa: busca as
corridas de cada um, agrega por dia e atualiza a planilha dele. `StravaClient`
e `RateLimiter` são criados **uma única vez** — o limite de vazão é da
aplicação inteira, dividido por todos os corredores, e um cliente por
participante derrotaria o controle.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime

from src.api.rate_limiter import RateLimiter
from src.api.strava_client import StravaClient
from src.models.corredor import Corredor
from src.repositories.activity_repository import ActivityRepository
from src.repositories.corredor_state_repository import CorredorStateRepository
from src.repositories.escrita_excel_repository import EscritaExcelRepository
from src.services.activity_service import ActivityService
from src.services.adocao_service import AdocaoService
from src.services.database_service import DatabaseService
from src.services.excel_service import ExcelService
from src.services.sync_service import SyncService
from src.utils.config import Config, ConfigError, load_config
from src.utils.errors import AuthorizationError, QuotaExhaustedError, RevokedTokenError, StravaError
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def main() -> int:
    """Sincroniza todos os corredores. Devolve o código de saída do processo."""
    try:
        config = load_config()
    except ConfigError as erro:
        # O logging ainda não subiu (o nível vem da própria config), então o
        # erro vai direto para o stderr.
        print(f"[StravaSync] {erro}", file=sys.stderr)
        return 1

    setup_logging(level=config.log_level, log_file=config.log_file)
    logger.info("StravaSync iniciando — %s", config.safe_summary())

    database = DatabaseService(config.database_path)
    try:
        database.init_schema()
        conn = database.connect()

        sync_service = _montar_sync_service(config, conn)
        corredor_state = CorredorStateRepository(conn)

        falhas = sincronizar_todos(config, sync_service, corredor_state)
    finally:
        database.close()

    if falhas:
        logger.error("Sincronização terminou com %d corredor(es) com falha.", falhas)
        return 1

    logger.info("Sincronização finalizada — %d corredor(es).", len(config.corredores))
    return 0


def _montar_sync_service(config: Config, conn: sqlite3.Connection) -> SyncService:
    """Constrói o `SyncService` com uma instância única de cliente e limitador."""
    limiter = RateLimiter(
        pausa_s=config.strava_pausa_entre_chamadas_s,
        reserva=config.strava_reserva_de_vazao,
    )
    client = StravaClient(
        config.strava_client_id,
        config.strava_client_secret,
        limiter=limiter,
        timeout_s=config.strava_timeout_s,
    )
    activity_repository = ActivityRepository(conn)
    excel_service = ExcelService()

    return SyncService(
        client=client,
        activity_service=ActivityService(),
        activity_repository=activity_repository,
        corredor_state_repository=CorredorStateRepository(conn),
        excel_service=excel_service,
        escrita_excel_repository=EscritaExcelRepository(conn),
        adocao_service=AdocaoService(excel_service, activity_repository),
    )


def sincronizar_todos(
    config: Config, sync_service: SyncService, corredor_state: CorredorStateRepository
) -> int:
    """Percorre o cadastro e devolve quantos corredores falharam.

    Cada corredor é isolado: com dezenas de participantes, um token expirado ou
    uma planilha aberta no Excel não pode interromper a coleta dos demais. A
    exceção é a cota diária: `QuotaExhaustedError` não é falha de ninguém —
    os corredores restantes ficam **pendentes**, e a execução para.
    """
    ordenados = _ordenar_por_prioridade(config.corredores, corredor_state)

    falhas = 0
    for indice, corredor in enumerate(ordenados):
        if _precisa_reinscricao(corredor, corredor_state):
            continue

        try:
            sync_service.sincronizar(corredor)
        except QuotaExhaustedError as erro:
            pendentes = [c.id for c in ordenados[indice:]]
            logger.error(
                "%s — %d corredor(es) ficam pendentes para a próxima execução: %s",
                erro,
                len(pendentes),
                ", ".join(pendentes),
            )
            break
        except RevokedTokenError as erro:
            # Já marcado para reinscrição por `auth.obter_access_token`/`chamar_renovando`.
            logger.error("Corredor %s precisa reinscrever-se: %s", corredor, erro)
            falhas += 1
            _registrar_falha_e_alertar(corredor, corredor_state, config)
        except (AuthorizationError, StravaError) as erro:
            logger.error("Corredor %s: %s", corredor, erro)
            falhas += 1
            _registrar_falha_e_alertar(corredor, corredor_state, config)
        except Exception:
            # Banco indisponível, planilha aberta no Excel, bug inesperado —
            # qualquer falha fora do vocabulário do Strava ainda isola o
            # corredor em vez de derrubar a execução inteira.
            logger.exception("Falha ao sincronizar o corredor %s", corredor)
            falhas += 1
            _registrar_falha_e_alertar(corredor, corredor_state, config)
    return falhas


def _ordenar_por_prioridade(
    corredores: tuple[Corredor, ...], corredor_state: CorredorStateRepository
) -> list[Corredor]:
    """Corredores sincronizados há mais tempo (ou nunca) vêm primeiro.

    Sem isso, uma `QuotaExhaustedError` sempre deixaria pendente o mesmo grupo
    no fim da lista do `corredores.toml` — com 50+ participantes, a rotina
    nunca chegaria a eles. `sorted` é estável: corredores empatados (o comum
    para um banco novo, todos `None`) mantêm a ordem do cadastro.
    """
    nunca = datetime.min.replace(tzinfo=UTC)

    def chave(corredor: Corredor) -> datetime:
        estado = corredor_state.buscar(corredor.id)
        if estado is None or estado.ultima_sincronizacao is None:
            return nunca
        return estado.ultima_sincronizacao

    return sorted(corredores, key=chave)


def _registrar_falha_e_alertar(
    corredor: Corredor, corredor_state: CorredorStateRepository, config: Config
) -> None:
    """Grava a falha no estado persistido e solta um alerta a cada N seguidas.

    Alertar a cada múltiplo do limiar (e não a cada falha a partir dele) evita
    um `CRITICAL` novo em toda execução depois que o problema já foi visto,
    mas ainda lembra periodicamente enquanto ele não for corrigido.
    """
    novas_falhas = corredor_state.registrar_falha(corredor.id)
    if novas_falhas % config.alerta_falhas_consecutivas == 0:
        logger.critical(
            "Corredor %s falhou %d execuções seguidas — considere reinscrever: "
            "python -m src.inscricao link %s",
            corredor,
            novas_falhas,
            corredor.id,
        )


def _precisa_reinscricao(corredor: Corredor, corredor_state: CorredorStateRepository) -> bool:
    """True (e loga) se o corredor está marcado para reinscrição.

    Checagem **antes** de tentar sincronizar: pular aqui não gasta nenhuma
    requisição, o que importa com a cota dividida por 50+ participantes. Não
    conta como falha desta execução — é um estado já conhecido, não uma
    descoberta nova.
    """
    estado = corredor_state.buscar(corredor.id)
    if estado is None or not estado.precisa_reinscricao:
        return False

    logger.warning(
        "Corredor %s pulado: precisa reinscrição (%s). Gere um link novo: "
        "python -m src.inscricao link %s",
        corredor,
        estado.motivo_reinscricao or "motivo não registrado",
        corredor.id,
    )
    return True


if __name__ == "__main__":
    sys.exit(main())
