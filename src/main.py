"""Ponto de entrada da aplicação StravaSync.

Orquestra a sincronização: autentica no Strava, busca corridas, agrega por dia
e atualiza a planilha de carga (ACWR). A lógica real é implementada nas Fases 3-6;
por enquanto este módulo carrega a configuração e liga o logging (Fase 2).
"""
from __future__ import annotations

import sys

from src.utils.config import Config, ConfigError, load_config
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def main() -> int:
    """Executa uma sincronização. Devolve o código de saída do processo."""
    try:
        config = load_config()
    except ConfigError as erro:
        # O logging ainda não subiu (o nível vem da própria config), então o
        # erro vai direto para o stderr.
        print(f"[StravaSync] {erro}", file=sys.stderr)
        return 1

    setup_logging(level=config.log_level, log_file=config.log_file)
    logger.info("StravaSync iniciando — %s", config.safe_summary())

    executar_sincronizacao(config)

    logger.info("Sincronização finalizada.")
    return 0


def executar_sincronizacao(config: Config) -> None:
    """Roda o fluxo de sincronização. (Stub — Fase 6.)"""
    # TODO(Fase 6): construir e executar o SyncService.
    logger.warning("Sincronização ainda não implementada (Fases 3-6 pendentes).")


if __name__ == "__main__":
    sys.exit(main())
