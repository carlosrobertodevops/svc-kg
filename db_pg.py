"""Acesso direto ao Postgres do mondaha via psycopg 3 (async + pool).

Substitui a leitura HTTP PostgREST por chamadas diretas à função SQL
`public.get_graph_membros(bigint, boolean, int)`, que devolve um único
`jsonb` no formato `{"nodes": [...], "edges": [...]}`.

Interface pública congelada:
    fetch_graph(faccao_id, include_co, max_pairs) -> dict
    pg_ping() -> bool
    ensure_schema() -> None
    backend_ok() -> bool
    close_pool() -> None
"""

from __future__ import annotations

import json
import logging
import os

from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("db_pg")

DATABASE_URL: str = os.getenv("DATABASE_URL", "")

# Constante int64 fixa para o advisory lock de aplicação do schema.
# Serializa `ensure_schema()` entre múltiplos workers (gunicorn) para
# evitar corrida no `CREATE OR REPLACE FUNCTION` (duplicate key pg_proc).
_SCHEMA_ADVISORY_LOCK: int = 918273645

# Pool singleton (lazy): criado na 1ª chamada que precisar de conexão.
_pool: AsyncConnectionPool | None = None


def _mask_dsn(dsn: str) -> str:
    """Mascara a senha da DATABASE_URL para logs seguros."""
    if not dsn:
        return ""
    try:
        # postgresql://user:senha@host:port/db  ->  postgresql://user:***@host...
        prefix, rest = dsn.split("://", 1)
        if "@" in rest and ":" in rest.split("@", 1)[0]:
            creds, tail = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{prefix}://{user}:***@{tail}"
        return dsn
    except Exception:
        return "<dsn>"


async def _get_pool() -> AsyncConnectionPool:
    """Retorna o pool singleton, criando-o (aberto) na 1ª chamada."""
    global _pool
    if _pool is None:
        pool = AsyncConnectionPool(
            conninfo=DATABASE_URL,
            min_size=1,
            max_size=int(os.getenv("PG_POOL_MAX", "10")),
            timeout=float(os.getenv("PG_POOL_TIMEOUT", "10")),
            open=False,
        )
        await pool.open()
        _pool = pool
    return _pool


async def fetch_graph(faccao_id: int | None, include_co: bool, max_pairs: int) -> dict:
    """Executa `get_graph_membros` e devolve `{'nodes': [...], 'edges': [...]}`.

    Se a função retornar None/vazio, devolve `{'nodes': [], 'edges': []}`.
    """
    pool = await _get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT public.get_graph_membros(%s, %s, %s)",
            (faccao_id, include_co, max_pairs),
        )
        row = await cur.fetchone()

    value = row[0] if row else None
    if value is None:
        return {"nodes": [], "edges": []}

    # psycopg3 devolve jsonb como dict quando o adapter está registrado;
    # se vier como str, faz o parse manual por robustez.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {"nodes": [], "edges": []}

    if not isinstance(value, dict):
        return {"nodes": [], "edges": []}

    return {
        "nodes": value.get("nodes") or [],
        "edges": value.get("edges") or [],
    }


async def pg_ping() -> bool:
    """Executa `SELECT 1`. True se o banco respondeu, False em qualquer erro."""
    try:
        pool = await _get_pool()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT 1")
            await cur.fetchone()
        return True
    except Exception as exc:
        logger.error("pg_ping falhou: %s", exc)
        return False


async def ensure_schema() -> None:
    """Instala/atualiza a função + índices via `db/mondaha_install.sql` (idempotente).

    Fail-soft: qualquer erro é logado e NÃO propaga (não derruba o startup).
    """
    if not DATABASE_URL:
        logger.warning("ensure_schema: DATABASE_URL vazia; schema nao aplicado")
        return

    sql_path = os.path.join(os.path.dirname(__file__), "db", "mondaha_install.sql")
    if not os.path.exists(sql_path):
        logger.warning("ensure_schema: arquivo nao encontrado: %s", sql_path)
        return

    try:
        with open(sql_path, encoding="utf-8") as fh:
            sql_text = fh.read()
        pool = await _get_pool()
        async with pool.connection() as conn:
            # AUTOCOMMIT: sem transação aberta, cada statement do arquivo comita
            # isoladamente. Assim uma falha pontual NÃO aborta os seguintes (evita
            # "current transaction is aborted", que mascarava o erro real) e o
            # advisory lock continua serializando os 2 workers do gunicorn.
            await conn.set_autocommit(True)
            async with conn.cursor() as cur:
                # Serializa a aplicação do schema entre workers: só um aplica
                # por vez (session-level advisory lock na mesma conexão).
                # Evita corrida no CREATE OR REPLACE FUNCTION (dup key pg_proc).
                await cur.execute(
                    "SELECT pg_advisory_lock(%s)", (_SCHEMA_ADVISORY_LOCK,)
                )
                try:
                    try:
                        await cur.execute(sql_text)
                        logger.info("ensure_schema: schema aplicado (%s)", sql_path)
                    except Exception as sql_exc:
                        # Loga o erro REAL do statement (não o mascarado) e segue
                        # fail-soft — o SQL é idempotente e outro worker já aplicou.
                        logger.error(
                            "ensure_schema: falha ao aplicar SQL (%s): %s",
                            sql_path,
                            sql_exc,
                        )
                finally:
                    await cur.execute(
                        "SELECT pg_advisory_unlock(%s)", (_SCHEMA_ADVISORY_LOCK,)
                    )
    except Exception as exc:
        logger.error("ensure_schema falhou (%s): %s", _mask_dsn(DATABASE_URL), exc)


def backend_ok() -> bool:
    """True se DATABASE_URL está configurada (não vazia)."""
    return bool(DATABASE_URL)


async def close_pool() -> None:
    """Fecha o pool (para shutdown)."""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as exc:
            logger.error("close_pool falhou: %s", exc)
        finally:
            _pool = None
