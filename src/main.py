"""Ponto de entrada da aplicação StravaSync.

A cada execução, percorre **todos** os participantes da pesquisa: busca as
corridas de cada um, agrega por dia e atualiza a planilha dele. A lógica real é
implementada nas Fases 3-6; por enquanto este módulo carrega a configuração,
liga o logging e percorre o cadastro (Fase 2).
"""
from __future__ import annotations

import sys

from src.models.corredor import Corredor
from src.utils.config import Config, ConfigError, load_config
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

    falhas = sincronizar_todos(config)

    if falhas:
        logger.error("Sincronização terminou com %d corredor(es) com falha.", falhas)
        return 1

    logger.info("Sincronização finalizada — %d corredor(es).", len(config.corredores))
    return 0


def sincronizar_todos(config: Config) -> int:
    """Percorre o cadastro e devolve quantos corredores falharam.

    Cada corredor é isolado: com dezenas de participantes, um token expirado ou
    uma planilha aberta no Excel não pode interromper a coleta dos demais.
    """
    falhas = 0
    for corredor in config.corredores:
        try:
            sincronizar_corredor(config, corredor)
        except Exception:
            # Sem `raise`: a falha de um participante é registrada e a coleta segue.
            logger.exception("Falha ao sincronizar o corredor %s", corredor)
            falhas += 1
    return falhas


def sincronizar_corredor(config: Config, corredor: Corredor) -> None:
    """Sincroniza um único corredor. (Stub — Fase 6.)"""
    # TODO(Fase 6): construir e executar o SyncService para este corredor.
    logger.warning(
        "Corredor %s ainda não sincronizado: Fases 3-6 pendentes "
        "(planilha=%s, dia 1=%s, corte=%s).",
        corredor,
        corredor.excel_path.name,
        corredor.start_date.isoformat(),
        corredor.cutover_date.isoformat(),
    )


if __name__ == "__main__":
    sys.exit(main())
