# app.py
import os, json, hashlib
from typing import List, Optional, Tuple
from time import time

from fastapi import FastAPI, Query, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from fastapi.openapi.utils import get_openapi
import yaml

from psycopg_pool import AsyncConnectionPool
import httpx
from redis import asyncio as aioredis

# ---------------- ENV ----------------
def env_bool(v: Optional[str], default=False) -> bool:
    if v is None: return default
    return v.lower() in ("1", "true", "yes", "on")

APP_ENV = os.getenv("APP_ENV", "production")
PORT = int(os.getenv("PORT", "8080"))
WORKERS = int(os.getenv("WORKERS", "2"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")
CORS_ALLOW_CREDENTIALS = env_bool(os.getenv("CORS_ALLOW_CREDENTIALS", "false"))
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS")

SUPABASE_URL = os.getenv("SUPABASE_URL")           # ex: https://<proj>.supabase.co
SUPABASE_KEY = os.getenv("SUPABASE_KEY")           # service_role ou anon com RPC liberado
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")

CACHE_STATIC_MAX_AGE = int(os.getenv("CACHE_STATIC_MAX_AGE", "86400"))
CACHE_API_TTL = int(os.getenv("CACHE_API_TTL", "60"))

ENABLE_REDIS_CACHE = env_bool(os.getenv("ENABLE_REDIS_CACHE", "true"), True)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# ---------------- APP ----------------
app = FastAPI(
    title="svc-kg",
    version="1.3.0",
    description="Microserviço de Knowledge Graph (membros, facções, funções)",
    default_response_class=ORJSONResponse,
    swagger_ui_parameters={
        "displayRequestDuration": True,
        "docExpansion": "none",
        "defaultModelsExpandDepth": -1,
        "defaultModelExpandDepth": 0,
    },
)

# CORS por .env
allow_origins = [o.strip() for o in (CORS_ALLOW_ORIGINS or "*").split(",")]
allow_methods = [m.strip() for m in (CORS_ALLOW_METHODS or "*").split(",")]
allow_headers = [h.strip() for h in (CORS_ALLOW_HEADERS or "*").split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=allow_methods,
    allow_headers=allow_headers,
)

# GZip
app.add_middleware(GZipMiddleware, minimum_size=512)

# Static mounts (usados pelos volumes do compose)
if os.path.isdir("assets"):
    app.mount("/assets", StaticFiles(directory="assets"), name="assets")
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------- Recursos globais ----------------
pool: Optional[AsyncConnectionPool] = None
http_client: Optional[httpx.AsyncClient] = None
redis_client: Optional[aioredis.Redis] = None
_mem_cache: dict[str, Tuple[float, dict]] = {}

# ---------------- Modelos p/ docs ----------------
class Node(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    label: str
    type: str
    group: Optional[int] = None
    size: Optional[float] = None

class Edge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str
    target: str
    weight: Optional[float] = 1.0
    relation: Optional[str] = None

class GraphResponse(BaseModel):
    nodes: List[Node]
    edges: List[Edge]

# ---------------- Startup/Shutdown ----------------
@app.on_event("startup")
async def _startup():
    global http_client, pool, redis_client
    # Supabase HTTP client
    if SUPABASE_URL and SUPABASE_KEY:
        http_client = httpx.AsyncClient(
            base_url=SUPABASE_URL.rstrip("/"),
            timeout=httpx.Timeout(SUPABASE_TIMEOUT),
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
            },
        )
    # Fallback Postgres
    pool = AsyncConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10, kwargs={"autocommit": True})

    # Redis
    if ENABLE_REDIS_CACHE and REDIS_URL:
        redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)

@app.on_event("shutdown")
async def _shutdown():
    global http_client, pool, redis_client
    if http_client:
        await http_client.aclose()
    if pool:
        await pool.close()
    if redis_client:
        await redis_client.close()

# ---------------- Cache helpers ----------------
async def cache_get(key: str) -> Optional[dict]:
    if redis_client:
        raw = await redis_client.get(key)
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                return None
        return None
    hit = _mem_cache.get(key)
    if not hit:
        return None
    ts, data = hit
    if time() - ts > CACHE_API_TTL:
        _mem_cache.pop(key, None)
        return None
    return data

async def cache_set(key: str, data: dict, ttl: int):
    if redis_client:
        await redis_client.set(key, json.dumps(data), ex=ttl)
    else:
        _mem_cache[key] = (time(), data)

# ---------------- Helpers core ----------------
def etag_for(data: dict) -> str:
    import orjson
    return hashlib.sha1(orjson.dumps(data)).hexdigest()

def truncate_preview(data: dict, max_nodes: int, max_edges: int) -> dict:
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    if len(nodes) <= max_nodes and len(edges) <= max_edges:
        return data
    deg = {n["id"]: 0.0 for n in nodes}
    for e in edges:
        w = float(e.get("weight", 1.0) or 1.0)
        if e.get("source") in deg: deg[e["source"]] += w
        if e.get("target") in deg: deg[e["target"]] += w
    keep = {n["id"] for n in sorted(nodes, key=lambda n: deg.get(n["id"], 0.0), reverse=True)[:max_nodes]}
    edges2 = [e for e in edges if e.get("source") in keep and e.get("target") in keep]
    if len(edges2) > max_edges:
        edges2.sort(key=lambda e: float(e.get("weight", 1.0)), reverse=True)
        edges2 = edges2[:max_edges]
    nodes2 = [n for n in nodes if n["id"] in keep]
    return {"nodes": nodes2, "edges": edges2}

async def fetch_graph_via_supabase(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
    assert http_client is not None
    url = f"/rest/v1/rpc/{SUPABASE_RPC_FN}"
    body = {"p_faccao_id": faccao_id, "p_include_co": include_co, "p_max_pairs": max_pairs}
    r = await http_client.post(url, json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Supabase RPC error ({r.status_code}): {r.text}")
    data = r.json() if r.content else {"nodes": [], "edges": []}
    if isinstance(data, str):
        data = json.loads(data)
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    return data

async def fetch_graph_via_pg(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
    assert pool is not None
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # tenta et_graph_membros (compat) e cai para get_graph_membros
            try:
                await cur.execute("select public.et_graph_membros(%s,%s,%s);", (faccao_id, include_co, max_pairs))
                row = await cur.fetchone()
            except Exception:
                await cur.execute("select public.get_graph_membros(%s,%s,%s);", (faccao_id, include_co, max_pairs))
                row = await cur.fetchone()
            data = row[0] if row else {"nodes": [], "edges": []}
            if isinstance(data, str):
                data = json.loads(data)
            data.setdefault("nodes", [])
            data.setdefault("edges", [])
            return data

async def fetch_graph(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
    if SUPABASE_URL and SUPABASE_KEY:
        return await fetch_graph_via_supabase(faccao_id, include_co, max_pairs)
    return await fetch_graph_via_pg(faccao_id, include_co, max_pairs)

# ---------------- OpenAPI (usar /docs/openapi.yaml se existir) ----------------
def custom_openapi():
    if getattr(app, "openapi_schema", None):
        return app.openapi_schema
    yaml_path = os.path.join("docs", "openapi.yaml")
    if os.path.exists(yaml_path):
        with open(yaml_path, "r", encoding="utf-8") as f:
            spec = yaml.safe_load(f)
            app.openapi_schema = spec
            return app.openapi_schema
    # fallback: gerar automático
    app.openapi_schema = get_openapi(
        title=app.title, version=app.version, routes=app.routes, description=app.description
    )
    return app.openapi_schema

app.openapi = custom_openapi  # sobrescreve o gerador

# ---------------- Endpoints ----------------
@app.get("/health", summary="Health check")
async def health(response: Response):
    response.headers["Cache-Control"] = f"public, max-age={CACHE_STATIC_MAX_AGE}"
    return {
        "status": "ok",
        "service": "svc-kg",
        "env": APP_ENV,
        "cache": "redis" if (ENABLE_REDIS_CACHE and REDIS_URL) else "memory",
        "backend": "supabase" if (SUPABASE_URL and SUPABASE_KEY) else "postgres",
    }

@app.get(
    "/v1/graph/membros",
    response_model=GraphResponse,
    summary="Retorna grafo (via função SQL: et_graph_membros/get_graph_membros)"
)
async def graph_membros(
    response: Response,
    faccao_id: Optional[int] = Query(default=None, description="Filtra por facção (opcional)"),
    include_co: bool = Query(default=True, description="Inclui arestas inferidas (CO_*)"),
    max_pairs: int = Query(default=8000, ge=1, le=200000, description="Teto de pares inferidos"),
    max_nodes: int = Query(default=2000, ge=100, le=20000, description="Preview: limitar nós"),
    max_edges: int = Query(default=4000, ge=100, le=200000, description="Preview: limitar arestas"),
    cache: bool = Query(default=True, description="Usar cache")
):
    key = f"graph:{faccao_id}:{include_co}:{max_pairs}"
    data = await cache_get(key) if cache else None
    if data is None:
        data = await fetch_graph(faccao_id, include_co, max_pairs)
        if cache:
            await cache_set(key, data, CACHE_API_TTL)
    out = truncate_preview(data, max_nodes, max_edges)
    response.headers["ETag"] = etag_for(out)
    response.headers["Cache-Control"] = "public, max-age=30"
    return out

@app.get(
    "/v1/nodes/{node_id}/neighbors",
    response_model=GraphResponse,
    summary="Subgrafo de vizinhança (raio=1)"
)
async def neighbors(
    response: Response,
    node_id: str,
    include_co: bool = True,
    max_pairs: int = 3000
):
    data = await fetch_graph(None, include_co, max_pairs)
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    keep = {node_id}
    for e in edges:
        if e.get("source") == node_id: keep.add(e.get("target"))
        if e.get("target") == node_id: keep.add(e.get("source"))
    nodes2 = [n for n in nodes if n.get("id") in keep]
    edges2 = [e for e in edges if e.get("source") in keep and e.get("target") in keep]
    out = {"nodes": nodes2, "edges": edges2}
    response.headers["ETag"] = etag_for(out)
    response.headers["Cache-Control"] = "public, max-age=30"
    return out
