"""Estado mutável de um corredor — a linha da tabela `corredor_state`.

O `corredores.toml` declara *quem está na pesquisa*; o que muda a cada execução
mora aqui. O `refresh_token` é o caso mais importante: o Strava pode devolver um
token novo a cada renovação, e reescrever um arquivo editado à mão destruiria
comentários e formatação.

Duas datas parecidas com papéis diferentes:

- `ultima_sincronizacao` — **quando rodamos**. Serve para auditoria e para saber
  de onde a execução retoma.
- `ultimo_evento_em` — o `start_date` (UTC) mais recente que já vimos. É a base
  do `after=`. Usar o relógio da execução no lugar dele pularia **para sempre**
  uma corrida de domingo enviada na terça.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class EstadoCorredor:
    """O que o app sabe sobre um participante entre uma execução e outra."""

    corredor_id: str

    athlete_id: int | None = None
    scope: str | None = None

    # Segredos: fora do repr.
    refresh_token: str = field(default="", repr=False)
    access_token: str = field(default="", repr=False)
    access_token_expira_em: datetime | None = None

    ultima_sincronizacao: datetime | None = None
    ultimo_evento_em: datetime | None = None

    precisa_reinscricao: bool = False
    motivo_reinscricao: str | None = None
    atualizado_em: datetime | None = None

    @property
    def inscrito(self) -> bool:
        """True quando há token utilizável e nenhuma reinscrição pendente."""
        return bool(self.refresh_token) and not self.precisa_reinscricao

    def token_de_acesso_utilizavel(self, margem_s: float = 300) -> str | None:
        """Devolve o access token guardado se ele ainda valer, senão `None`.

        É o que torna a retomada barata: reexecutar dez minutos depois de uma
        interrupção não gasta uma requisição de renovação por corredor já
        processado — e a cota é da aplicação inteira.
        """
        if not self.access_token or self.access_token_expira_em is None:
            return None
        restante = (self.access_token_expira_em - datetime.now(UTC)).total_seconds()
        return self.access_token if restante > margem_s else None
