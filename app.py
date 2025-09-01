# src/app.py
import os, json, hashlib
from typing import List, Optional, Tuple
from time import time

from fastapi import FastAPI, Query, Response, HTTPException   # <- (alterado)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, ConfigDict

from psycopg_pool import AsyncConnectionPool

APP_NAME = os.getenv("APP_NAME", "svc-kg")
DB_URL   = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
CACHE_TTL   = int(os.getenv("CACHE_TTL", "30"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

app = FastAPI(
  title="svc-kg",
  version="1.1.1",
  description="Microserviço de Knowledge Graph",
  default_response_class=ORJSONResponse,
  swagger_ui_parameters={"docExpansion": "none", "displayRequestDuration": True}
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=512)

# ---- POOL: LAZY + resiliente (alterado) --------------------------------------
pool: Optional[AsyncConnectionPool] = None

def _new_pool() -> AsyncConnectionPool:
    # open=False => NÃO tenta conectar no startup
    return AsyncConnectionPool(
        conninfo=DB_URL,
        min_size=1, max_size=DB_POOL_MAX,
        kwargs={"autocommit": True},
        open=False
    )

@app.on_event("startup")
async def on_start():
    # apenas cria objeto; não abre conexão ainda
    global pool
    pool = _new_pool()

@app.on_event("shutdown")
async def on_stop():
    global pool
    if pool and pool.is_open:
        await pool.close()

async def _ensure_pool_open() -> bool:
    """Tenta abrir o pool na 1ª necessidade; NÃO derruba o app se falhar."""
    global pool
    if pool is None:
        pool = _new_pool()
    if not pool.is_open:
        try:
            await pool.open(wait=False)
        except Exception:
            return False
    return True
# -----------------------------------------------------------------------------

class Node(BaseModel):
  model_config = ConfigDict(extra="ignore")
  id: str; label: str; type: str
  group: Optional[int] = None
  size: Optional[float] = None

class Edge(BaseModel):
  model_config = ConfigDict(extra="ignore")
  source: str; target: str
  weight: Optional[float] = 1.0
  relation: Optional[str] = None

class GraphResponse(BaseModel):
  nodes: List[Node]; edges: List[Edge]

_CACHE: dict[str, Tuple[float, dict]] = {}
def cache_get(k: str): 
    it = _CACHE.get(k); 
    if not it: return None
    ts, v = it
    if time() - ts > CACHE_TTL:
        _CACHE.pop(k, None); return None
    return v
def cache_set(k: str, v: dict): _CACHE[k] = (time(), v)

def truncate_preview(data: dict, max_nodes: int, max_edges: int) -> dict:
    nodes = data.get("nodes", []); edges = data.get("edges", [])
    if len(nodes) <= max_nodes and len(edges) <= max_edges: return data
    deg = {n["id"]: 0.0 for n in nodes}
    for e in edges:
        w = float(e.get("weight", 1.0) or 1.0)
        if e.get("source") in deg: deg[e["source"]] += w
        if e.get("target") in deg: deg[e["target"]] += w
    keep = {n["id"] for n in sorted(nodes, key=lambda n: deg.get(n["id"],0.0), reverse=True)[:max_nodes]}
    edges2 = [e for e in edges if e.get("source") in keep and e.get("target") in keep]
    if len(edges2) > max_edges:
        edges2.sort(key=lambda e: float(e.get("weight",1.0)), reverse=True)
        edges2 = edges2[:max_edges]
    nodes2 = [n for n in nodes if n["id"] in keep]
    return {"nodes": nodes2, "edges": edges2}

def etag(data: dict) -> str:
    import orjson; return hashlib.sha1(orjson.dumps(data)).hexdigest()

@app.get("/")
async def root():  # facilita testagem via navegador
    return {"ok": True, "service": APP_NAME}

@app.get("/health")
async def health():
    # NUNCA acessa o DB aqui → health sempre 200
    open_ = bool(pool and pool.is_open)
    return {"status": "ok", "service": APP_NAME, "db_pool_open": open_}

async def _fetch_graph(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
    if not await _ensure_pool_open():
        # Retorna 503 em rotas que dependem de DB, mas não derruba o processo
        raise HTTPException(status_code=503, detail="Database unavailable")
    assert pool is not None
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("select public.get_graph_membros(%s,%s,%s);",
                              (faccao_id, include_co, max_pairs))
            row = await cur.fetchone()
            data = row[0] if row else {"nodes": [], "edges": []}
            if isinstance(data, str): data = json.loads(data)
            data.setdefault("nodes", []); data.setdefault("edges", [])
            return data

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
  key = f"{faccao_id}:{include_co}:{max_pairs}"
  data = cache_get(key) if cache else None
  if data is None:
    data = await _fetch_graph(faccao_id, include_co, max_pairs)
    if cache: cache_set(key, data)
  data = truncate_preview(data, max_nodes, max_edges)
  response.headers["ETag"] = etag(data)
  response.headers["Cache-Control"] = "public, max-age=30"
  return data

@app.get("/v1/nodes/{node_id}/neighbors", response_model=GraphResponse)
async def neighbors(response: Response, node_id: str,
                    include_co: bool = True, max_pairs: int = 3000):
  data = await _fetch_graph(None, include_co, max_pairs)
  nodes = data.get("nodes", []); edges = data.get("edges", [])
  keep = {node_id}
  for e in edges:
    if e.get("source") == node_id: keep.add(e.get("target"))
    if e.get("target") == node_id: keep.add(e.get("source"))
  nodes2 = [n for n in nodes if n.get("id") in keep]
  edges2 = [e for e in edges if e.get("source") in keep and e.get("target") in keep]
  out = {"nodes": nodes2, "edges": edges2}
  response.headers["ETag"] = etag(out)
  response.headers["Cache-Control"] = "public, max-age=30"
  return out
