# src/app.py
import os, json, hashlib
from typing import List, Optional, Tuple
from time import time

from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, ConfigDict

from psycopg_pool import AsyncConnectionPool

APP_NAME = os.getenv("APP_NAME", "svc-kg")
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
CACHE_TTL = int(os.getenv("CACHE_TTL", "30"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

app = FastAPI(
  title="svc-kg",
  version="1.1.0",
  description="Microserviço de Knowledge Graph (membros, facções, funções)",
  default_response_class=ORJSONResponse,
  swagger_ui_parameters={"docExpansion": "none", "displayRequestDuration": True}
)

# CORS (dev)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
# Compressão
app.add_middleware(GZipMiddleware, minimum_size=512)

# Pool assíncrono
pool: Optional[AsyncConnectionPool] = None

@app.on_event("startup")
async def on_start():
  global pool
  pool = AsyncConnectionPool(conninfo=DB_URL, min_size=1, max_size=DB_POOL_MAX, kwargs={"autocommit": True})

@app.on_event("shutdown")
async def on_stop():
  if pool:
    await pool.close()

# ---------- Modelos (p/ docs) ----------
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

# ---------- Cache simples ----------
_CACHE: dict[str, Tuple[float, dict]] = {}

def cache_get(key: str) -> Optional[dict]:
  it = _CACHE.get(key)
  if not it: return None
  ts, val = it
  if time() - ts > CACHE_TTL:
    _CACHE.pop(key, None)
    return None
  return val

def cache_set(key: str, val: dict):
  _CACHE[key] = (time(), val)

async def fetch_graph(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
  assert pool is not None
  async with pool.connection() as conn:
    async with conn.cursor() as cur:
      await cur.execute("select public.get_graph_membros(%s,%s,%s);", (faccao_id, include_co, max_pairs))
      row = await cur.fetchone()
      data = row[0] if row else {"nodes": [], "edges": []}
      if isinstance(data, str):
        data = json.loads(data)
      data.setdefault("nodes", [])
      data.setdefault("edges", [])
      return data

def truncate_preview(data: dict, max_nodes: int, max_edges: int) -> dict:
  nodes = data.get("nodes", [])
  edges = data.get("edges", [])
  if len(nodes) <= max_nodes and len(edges) <= max_edges:
    return data

  # prioriza nós de maior grau
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
  import orjson
  return hashlib.sha1(orjson.dumps(data)).hexdigest()

# ---------- Endpoints ----------
@app.get("/health", summary="Health check")
async def health():
  return {"status": "ok", "service": APP_NAME}

@app.get("/v1/graph/membros", response_model=GraphResponse, summary="Retorna grafo via função SQL public.get_graph_membros")
async def graph_membros(
  response: Response,
  faccao_id: Optional[int] = Query(default=None, description="Filtra por facção"),
  include_co: bool = Query(default=True, description="Inclui arestas inferidas (CO_*)"),
  max_pairs: int = Query(default=8000, ge=1, le=200000, description="Teto pares inferidos"),
  max_nodes: int = Query(default=2000, ge=100, le=20000),
  max_edges: int = Query(default=4000, ge=100, le=200000),
  cache: bool = Query(default=True)
):
  key = f"{faccao_id}:{include_co}:{max_pairs}"
  data = cache_get(key) if cache else None
  if data is None:
    data = await fetch_graph(faccao_id, include_co, max_pairs)
    if cache: cache_set(key, data)

  data = truncate_preview(data, max_nodes, max_edges)
  response.headers["ETag"] = etag(data)
  response.headers["Cache-Control"] = "public, max-age=30"
  return data

@app.get("/v1/nodes/{node_id}/neighbors", response_model=GraphResponse, summary="Subgrafo de vizinhança (raio=1)")
async def neighbors(response: Response, node_id: str,
                    include_co: bool = True, max_pairs: int = 3000):
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
  response.headers["ETag"] = etag(out)
  response.headers["Cache-Control"] = "public, max-age=30"
  return out
