"""Ponto de entrada da aplicação StravaSync.

Orquestra a sincronização: autentica no Strava, busca corridas, agrega por dia
e atualiza a planilha de carga (ACWR). A lógica real é implementada nas Fases 3-6;
por enquanto este módulo apenas registra que a estrutura foi criada (Fase 1).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Executa uma sincronização. (Stub — Fase 6.)"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("StravaSync — estrutura inicial (Fase 1). Lógica ainda não implementada.")
    # TODO(Fase 6): construir e executar o SyncService.


if __name__ == "__main__":
    main()
