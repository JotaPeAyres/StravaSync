"""Agendamento da execução diária.

Acionado por um agendador **externo** — Windows Task Scheduler, cron,
Docker+cron ou GitHub Actions — uma vez ao dia, delegando para
:func:`src.main.main`. Não há loop nem `sleep` interno aqui de propósito: a
cadência é responsabilidade de quem chama, não deste módulo.

`executar()` é a rede de segurança que falta em `main.main()`: aquele função
já isola a falha de cada corredor e trata `ConfigError` antes do logging subir,
mas um bug de infraestrutura fora desse vocabulário (banco inacessível, disco
cheio) ainda poderia escapar como traceback cru. Um agendador não deve receber
isso — precisa de um código de saída e, sempre que possível, uma linha de log.
"""
from __future__ import annotations

import sys

from src.main import main as sincronizar
from src.utils.logger import get_logger

logger = get_logger(__name__)


def executar() -> int:
    """Roda a sincronização completa, sem deixar nenhuma exceção escapar."""
    try:
        return sincronizar()
    except Exception:
        logger.critical("Falha não tratada na execução agendada.", exc_info=True)
        print("[StravaSync] falha não tratada — veja o log.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(executar())
