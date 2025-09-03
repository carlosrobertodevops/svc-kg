# app.py
import os, json, hashlib, asyncio, logging, traceback, socket
from typing import List, Optional, Tuple
from time import time
from urllib.parse import urlparse

from fastapi import FastAPI, Query, Response, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict
import yaml
import httpx
from psycopg_pool import AsyncConnectionPool
from redis import asyncio as aioredis

def env_bool(v: Optional[str], default=False) -> bool:
    if v is None: return default
    return v.lower() in ("1", "true", "yes", "on")

def normalize_supabase_url(u: str) -> str:
    if not u: return ""
    u = u.strip().replace("/rest/v1", "")
    while u.endswith("/"): u = u[:-1]
    if not (u.startswith("http://") or u.startswith("https://")):
        u = "https://" + u
    return u

APP_ENV = os.getenv("APP_ENV", "production")
PORT = int(os.getenv("PORT", "8080"))
WORKERS = int(os.getenv("WORKERS", "2"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()

CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")
CORS_ALLOW_CREDENTIALS = env_bool(os.getenv("CORS_ALLOW_CREDENTIALS", "false"))
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS")

SUPABASE_URL_RAW = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_URL = normalize_supabase_url(SUPABASE_URL_RAW)
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

CACHE_STATIC_MAX_AGE = int(os.getenv("CACHE_STATIC_MAX_AGE", "86400"))
CACHE_API_TTL = int(os.getenv("CACHE_API_TTL", "60"))

ENABLE_REDIS_CACHE = env_bool(os.getenv("ENABLE_REDIS_CACHE", "true"), True)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0").strip()

BACKEND_MODE = "supabase" if (SUPABASE_URL and SUPABASE_KEY) else ("postgres" if DATABASE_URL else "none")

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("svc-kg")

app = FastAPI(
    title="svc-kg",
    version="1.5.2",
    description="Microserviço de Knowledge Graph (membros, facções, funções)",
    default_response_class=ORJSONResponse,
    swagger_ui_parameters={"displayRequestDuration": True, "docExpansion": "none",
                           "defaultModelsExpandDepth": -1, "defaultModelExpandDepth": 0},
)

allow_origins = [o.strip() for o in (CORS_ALLOW_ORIGINS or "*").split(",")]
allow_methods = [m.strip() for m in (CORS_ALLOW_METHODS or "*").split(",")]
allow_headers = [h.strip() for h in (CORS_ALLOW_HEADERS or "*").split(",")]
app.add_middleware(CORSMiddleware, allow_origins=allow_origins,
                   allow_credentials=CORS_ALLOW_CREDENTIALS,
                   allow_methods=allow_methods, allow_headers=allow_headers)
app.add_middleware(GZipMiddleware, minimum_size=512)

if os.path.isdir("assets"): app.mount("/assets", StaticFiles(directory="assets"), name="assets")
if os.path.isdir("static"): app.mount("/static", StaticFiles(directory="static"), name="static")

http_client: Optional[httpx.AsyncClient] = None
pool: Optional[AsyncConnectionPool] = None
redis_client: Optional[aioredis.Redis] = None
_mem_cache: dict[str, Tuple[float, dict]] = {}

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

@app.on_event("startup")
async def _startup():
    global http_client, pool, redis_client
    log.info(f"Starting svc-kg v{app.version} | mode={BACKEND_MODE} | port={PORT} | supabase={SUPABASE_URL}")
    if BACKEND_MODE == "supabase":
        http_client = httpx.AsyncClient(
            base_url=SUPABASE_URL,
            timeout=httpx.Timeout(SUPABASE_TIMEOUT),
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"},
        )
    if BACKEND_MODE == "postgres":
        pool = AsyncConnectionPool(conninfo=DATABASE_URL, min_size=0, max_size=10, kwargs={"autocommit": True})
    if ENABLE_REDIS_CACHE and REDIS_URL:
        try:
            redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
            await asyncio.wait_for(redis_client.ping(), timeout=2)
            log.info("Redis OK")
        except Exception as e:
            log.warning(f"Redis indisponível, fallback memória: {e}")
            redis_client = None

@app.on_event("shutdown")
async def _shutdown():
    if http_client: await http_client.aclose()
    if pool: await pool.close()
    if redis_client: await redis_client.close()

@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail),
                         "status_code": exc.status_code, "path": str(request.url.path)})

@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    log.error("Unhandled exception: %s\n%s", exc, "".join(traceback.format_exc()[-1000:]))
    return JSONResponse(status_code=500, content={"error": "internal_error", "message": str(exc)[:500],
                                                  "path": str(request.url.path)})

def _now() -> float: return time()

async def cache_get(key: str) -> Optional[dict]:
    if redis_client:
        try:
            raw = await redis_client.get(key)
            if raw: return json.loads(raw)
        except Exception as e:
            log.debug(f"cache_get redis falhou: {e}")
    hit = _mem_cache.get(key)
    if not hit: return None
    ts, data = hit
    if _now() - ts > CACHE_API_TTL:
        _mem_cache.pop(key, None); return None
    return data

async def cache_set(key: str, data: dict, ttl: int):
    if redis_client:
        try:
            await redis_client.set(key, json.dumps(data), ex=ttl); return
        except Exception as e:
            log.debug(f"cache_set redis falhou: {e}")
    _mem_cache[key] = (_now(), data)

def etag_for(data: dict) -> str:
    import orjson; return hashlib.sha1(orjson.dumps(data)).hexdigest()

def truncate_preview(data: dict, max_nodes: int, max_edges: int) -> dict:
    nodes = data.get("nodes", []); edges = data.get("edges", [])
    if len(nodes) <= max_nodes and len(edges) <= max_edges: return data
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
    if http_client is None:
        raise HTTPException(status_code=503, detail="Supabase client não inicializado")
    url = f"/rest/v1/rpc/{SUPABASE_RPC_FN}"
    body = {"p_faccao_id": faccao_id, "p_include_co": include_co, "p_max_pairs": max_pairs}
    r = await http_client.post(url, json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Supabase RPC {SUPABASE_RPC_FN} falhou ({r.status_code}): {r.text[:400]}")
    try:
        data = r.json() if r.content else {"nodes": [], "edges": []}
    except Exception:
        data = {"nodes": [], "edges": []}
    if isinstance(data, str):
        try: data = json.loads(data)
        except Exception: data = {"nodes": [], "edges": []}
    data.setdefault("nodes", []); data.setdefault("edges", [])
    return data

async def fetch_graph_via_pg(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
    if pool is None:
        raise HTTPException(status_code=503, detail="Pool Postgres não inicializado")
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute("select public.et_graph_membros(%s,%s,%s);", (faccao_id, include_co, max_pairs))
                row = await cur.fetchone()
            except Exception as e:
                await cur.execute("select public.get_graph_membros(%s,%s,%s);", (faccao_id, include_co, max_pairs))
                row = await cur.fetchone()
            data = row[0] if row else {"nodes": [], "edges": []}
            if isinstance(data, str):
                try: data = json.loads(data)
                except Exception: data = {"nodes": [], "edges": []}
            data.setdefault("nodes", []); data.setdefault("edges", [])
            return data

async def fetch_graph(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
    if BACKEND_MODE == "supabase": return await fetch_graph_via_supabase(faccao_id, include_co, max_pairs)
    if BACKEND_MODE == "postgres": return await fetch_graph_via_pg(faccao_id, include_co, max_pairs)
    raise HTTPException(status_code=500, detail="Nenhum backend configurado (defina SUPABASE_* ou DATABASE_URL)")

def custom_openapi():
    if getattr(app, "openapi_schema", None): return app.openapi_schema
    yaml_path = os.path.join("docs", "openapi.yaml")
    if os.path.exists(yaml_path):
        with open(yaml_path, "r", encoding="utf-8") as f:
            app.openapi_schema = yaml.safe_load(f); return app.openapi_schema
    app.openapi_schema = get_openapi(title=app.title, version=app.version, routes=app.routes, description=app.description)
    return app.openapi_schema
app.openapi = custom_openapi

@app.get("/live")
async def live(): return {"status": "live", "service": "svc-kg"}

@app.get("/ready")
async def ready():
    info = {"redis": False, "backend": BACKEND_MODE, "backend_ok": False}
    # DNS preflight (quando supabase)
    if BACKEND_MODE == "supabase":
        parsed = urlparse(SUPABASE_URL)
        host = parsed.hostname or ""
        info["supabase_host"] = host
        try:
            # tenta resolver DNS com timeout curto
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(loop.run_in_executor(None, socket.getaddrinfo, host, 443), timeout=2.0)
            info["dns_ok"] = True
        except Exception as e:
            info["dns_ok"] = False
            info["error"] = f"DNS fail for {host}: {e}"
            return ORJSONResponse(info, status_code=503)

    if redis_client:
        try:
            info["redis"] = bool(await asyncio.wait_for(redis_client.ping(), timeout=1.5))
        except Exception:
            info["redis"] = False

    try:
        if BACKEND_MODE == "supabase":
            _ = await fetch_graph_via_supabase(None, False, 1)
            info["backend_ok"] = True
        elif BACKEND_MODE == "postgres":
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("select 1;"); await cur.fetchone()
            info["backend_ok"] = True
        else:
            info["backend_ok"] = False
    except Exception as e:
        info["backend_ok"] = False
        info["error"] = str(e)[:400]
    return ORJSONResponse(info, status_code=(200 if info["backend_ok"] else 503))

@app.get("/debug/config")
async def debug_config():
    return {
        "env": APP_ENV, "port": PORT,
        "backend_mode": BACKEND_MODE,
        "supabase_url_raw": SUPABASE_URL_RAW,
        "supabase_url": SUPABASE_URL,
        "rpc_function": SUPABASE_RPC_FN,
        "has_database_url": bool(DATABASE_URL),
        "redis_enabled": ENABLE_REDIS_CACHE, "has_redis_url": bool(REDIS_URL),
    }

@app.get("/health")
async def health(response: Response):
    response.headers["Cache-Control"] = f"public, max-age={CACHE_STATIC_MAX_AGE}"
    return {"status":"ok","service":"svc-kg","env":APP_ENV,
            "cache":"redis" if (ENABLE_REDIS_CACHE and REDIS_URL and redis_client) else "memory",
            "backend": BACKEND_MODE}

@app.get("/v1/graph/membros", response_model=GraphResponse)
async def graph_membros(
    response: Response,
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True)
):
    key = f"graph:{faccao_id}:{include_co}:{max_pairs}"
    data = await cache_get(key) if cache else None
    if data is None:
        data = await fetch_graph(faccao_id, include_co, max_pairs)
        if cache: await cache_set(key, data, CACHE_API_TTL)
    out = truncate_preview(data, max_nodes, max_edges)
    response.headers["ETag"] = etag_for(out); response.headers["Cache-Control"] = "public, max-age=30"
    return out

@app.get("/graph/members", response_model=GraphResponse)
async def graph_members_compat(
    response: Response,
    p_faccao_id: Optional[int] = Query(default=None),
    p_include_co: bool = Query(default=True),
    p_max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True),
):
    key = f"graph:{p_faccao_id}:{p_include_co}:{p_max_pairs}"
    data = await cache_get(key) if cache else None
    if data is None:
        data = await fetch_graph(p_faccao_id, p_include_co, p_max_pairs)
        if cache: await cache_set(key, data, CACHE_API_TTL)
    out = truncate_preview(data, max_nodes, max_edges)
    response.headers["ETag"] = etag_for(out); response.headers["Cache-Control"] = "public, max-age=30"
    return out

@app.get("/v1/nodes/{node_id}/neighbors", response_model=GraphResponse)
async def neighbors(response: Response, node_id: str, include_co: bool = True, max_pairs: int = 3000):
    data = await fetch_graph(None, include_co, max_pairs)
    nodes = data.get("nodes", []); edges = data.get("edges", [])
    keep = {node_id}
    for e in edges:
        if e.get("source") == node_id: keep.add(e.get("target"))
        if e.get("target") == node_id: keep.add(e.get("source"))
    nodes2 = [n for n in nodes if n.get("id") in keep]
    edges2 = [e for e in edges if e.get("source") in keep and e.get("target") in keep]
    out = {"nodes": nodes2, "edges": edges2}
    response.headers["ETag"] = etag_for(out); response.headers["Cache-Control"] = "public, max-age=30"
    return out
