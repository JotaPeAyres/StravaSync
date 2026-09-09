"""Persistência do estado mutável de cada corredor.

Este repositório **não conhece o Strava**: recebe modelos e devolve modelos. A
única regra de negócio que ele carrega é a de não apagar informação — ver
`salvar_token`.

Cada operação faz `commit()` na hora. Isso é o que torna a execução retomável:
se a coleta morrer no corredor 60, os 59 anteriores já estão gravados e a
próxima execução continua dali em vez de recomeçar — o que importa quando a cota
do Strava é da aplicação inteira.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from src.models.estado_corredor import EstadoCorredor
from src.models.strava_token import StravaToken
from src.services.database_service import de_texto, para_texto
from src.utils.errors import StateError

_COLUNAS = """
    corredor_id, athlete_id, scope, refresh_token, access_token,
    access_token_expira_em, ultima_sincronizacao, ultimo_evento_em,
    precisa_reinscricao, motivo_reinscricao, atualizado_em, falhas_consecutivas
"""


class CorredorStateRepository:
    """Lê e grava a tabela `corredor_state`."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        agora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._conn = conn
        self._agora = agora

    def buscar(self, corredor_id: str) -> EstadoCorredor | None:
        """Devolve o estado gravado, ou `None` se o corredor nunca foi tocado."""
        linha = self._executar(
            f"SELECT {_COLUNAS} FROM corredor_state WHERE corredor_id = ?", (corredor_id,)
        ).fetchone()
        return _para_modelo(linha) if linha is not None else None

    def listar(self) -> tuple[EstadoCorredor, ...]:
        """Todos os estados gravados, em ordem de id."""
        linhas = self._executar(
            f"SELECT {_COLUNAS} FROM corredor_state ORDER BY corredor_id"
        ).fetchall()
        return tuple(_para_modelo(linha) for linha in linhas)

    def salvar_token(
        self, corredor_id: str, token: StravaToken, *, scope: str | None = None
    ) -> None:
        """Grava o par de tokens, preservando o que a renovação não devolve.

        A renovação responde sem `athlete_id` e sem `scope`. Um `UPDATE` ingênuo
        gravaria `NULL` neles e apagaria justamente a prova de qual escopo o
        participante concedeu — o dado que existe para não termos de procurar a
        pessoa de novo meses depois. Daí o `COALESCE`: valor novo quando existe,
        valor antigo quando não.

        Gravar um token também **limpa** qualquer marcação de reinscrição: acabou
        de funcionar, logo não está mais revogado.
        """
        self._executar(
            f"""
            INSERT INTO corredor_state ({_COLUNAS}) VALUES (
                :corredor_id, :athlete_id, :scope, :refresh_token, :access_token,
                :access_token_expira_em, NULL, NULL, 0, NULL, :atualizado_em, 0
            )
            ON CONFLICT(corredor_id) DO UPDATE SET
                athlete_id             = COALESCE(excluded.athlete_id, corredor_state.athlete_id),
                scope                  = COALESCE(excluded.scope, corredor_state.scope),
                refresh_token          = excluded.refresh_token,
                access_token           = excluded.access_token,
                access_token_expira_em = excluded.access_token_expira_em,
                precisa_reinscricao    = 0,
                motivo_reinscricao     = NULL,
                atualizado_em          = excluded.atualizado_em
            """,
            {
                "corredor_id": corredor_id,
                "athlete_id": token.athlete_id,
                "scope": token.scope or scope,
                "refresh_token": token.refresh_token,
                "access_token": token.access_token,
                "access_token_expira_em": para_texto(token.expira_em),
                "atualizado_em": para_texto(self._agora()),
            },
        )
        self._conn.commit()

    def registrar_sincronizacao(
        self, corredor_id: str, *, ultimo_evento_em: datetime | None = None
    ) -> None:
        """Marca que este corredor foi sincronizado agora.

        `ultimo_evento_em` só avança, nunca retrocede: ele é a base do `after=`
        e voltar no tempo faria a próxima execução repaginar histórico já visto.

        Também zera `falhas_consecutivas`: só é chamado quando a sincronização
        deste corredor terminou com sucesso, então qualquer sequência de falhas
        anterior acabou de ser interrompida.
        """
        estado = self.buscar(corredor_id)
        anterior = estado.ultimo_evento_em if estado is not None else None
        evento = max(filter(None, (anterior, ultimo_evento_em)), default=None)

        self._executar(
            """
            INSERT INTO corredor_state (corredor_id, ultima_sincronizacao,
                                        ultimo_evento_em, atualizado_em)
            VALUES (:corredor_id, :ultima_sincronizacao, :ultimo_evento_em, :atualizado_em)
            ON CONFLICT(corredor_id) DO UPDATE SET
                ultima_sincronizacao = excluded.ultima_sincronizacao,
                ultimo_evento_em     = excluded.ultimo_evento_em,
                falhas_consecutivas  = 0,
                atualizado_em        = excluded.atualizado_em
            """,
            {
                "corredor_id": corredor_id,
                "ultima_sincronizacao": para_texto(self._agora()),
                "ultimo_evento_em": para_texto(evento),
                "atualizado_em": para_texto(self._agora()),
            },
        )
        self._conn.commit()

    def marcar_reinscricao(self, corredor_id: str, motivo: str) -> None:
        """Registra que o participante precisa autorizar de novo.

        A marca serve para pular o corredor nas execuções seguintes sem gastar
        requisição: insistir num token morto todo dia consome a cota que os
        outros participantes precisam.
        """
        self._executar(
            """
            INSERT INTO corredor_state (corredor_id, precisa_reinscricao,
                                        motivo_reinscricao, atualizado_em)
            VALUES (:corredor_id, 1, :motivo, :atualizado_em)
            ON CONFLICT(corredor_id) DO UPDATE SET
                precisa_reinscricao = 1,
                motivo_reinscricao  = excluded.motivo_reinscricao,
                access_token        = '',
                atualizado_em       = excluded.atualizado_em
            """,
            {
                "corredor_id": corredor_id,
                "motivo": motivo,
                "atualizado_em": para_texto(self._agora()),
            },
        )
        self._conn.commit()

    def registrar_falha(self, corredor_id: str) -> int:
        """Incrementa o contador de falhas seguidas deste corredor e devolve o novo valor.

        Chamado pelo `main.py` quando a sincronização de um corredor falha (fora
        de `QuotaExhaustedError`, que não é falha dele). É a base do alerta da
        Fase 7 — sem isso, "falhou várias execuções seguidas" não tem como ser
        detectado, porque `falhas` em `main.py` só existe durante a execução
        atual.
        """
        linha = self._executar(
            """
            INSERT INTO corredor_state (corredor_id, falhas_consecutivas, atualizado_em)
            VALUES (:corredor_id, 1, :atualizado_em)
            ON CONFLICT(corredor_id) DO UPDATE SET
                falhas_consecutivas = corredor_state.falhas_consecutivas + 1,
                atualizado_em       = excluded.atualizado_em
            RETURNING falhas_consecutivas
            """,
            {"corredor_id": corredor_id, "atualizado_em": para_texto(self._agora())},
        ).fetchone()[0]
        self._conn.commit()
        return linha

    def limpar_reinscricao(self, corredor_id: str) -> None:
        """Desfaz a marcação (o participante autorizou de novo)."""
        self._executar(
            """
            UPDATE corredor_state
               SET precisa_reinscricao = 0, motivo_reinscricao = NULL, atualizado_em = ?
             WHERE corredor_id = ?
            """,
            (para_texto(self._agora()), corredor_id),
        )
        self._conn.commit()

    def _executar(self, sql: str, parametros: tuple | dict = ()) -> sqlite3.Cursor:
        """Executa e traduz falhas do SQLite para `StateError`."""
        try:
            return self._conn.execute(sql, parametros)
        except sqlite3.Error as erro:
            raise StateError(f"falha ao acessar o estado dos corredores: {erro}") from erro


def _para_modelo(linha: sqlite3.Row) -> EstadoCorredor:
    """Converte uma linha da tabela no modelo, normalizando datas e booleano."""
    return EstadoCorredor(
        corredor_id=linha["corredor_id"],
        athlete_id=linha["athlete_id"],
        scope=linha["scope"],
        refresh_token=linha["refresh_token"] or "",
        access_token=linha["access_token"] or "",
        access_token_expira_em=de_texto(linha["access_token_expira_em"]),
        ultima_sincronizacao=de_texto(linha["ultima_sincronizacao"]),
        ultimo_evento_em=de_texto(linha["ultimo_evento_em"]),
        precisa_reinscricao=bool(linha["precisa_reinscricao"]),
        motivo_reinscricao=linha["motivo_reinscricao"],
        atualizado_em=de_texto(linha["atualizado_em"]),
        falhas_consecutivas=linha["falhas_consecutivas"],
    )
