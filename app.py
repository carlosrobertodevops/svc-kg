import os
import hashlib
import json
from typing import Optional

from fastapi import FastAPI, Query, Header, Response, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import ORJSONResponse, HTMLResponse, FileResponse

from cache import get_cache
from config import Settings, get_settings
from supabase_client import SupabaseRPC
from graph_builder import build_pyvis_html, normalize_graph_schema, truncate_preview
from utils import make_cache_headers, compute_etag

settings: Settings = get_settings()

app = FastAPI(
    title="svc-kg",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()],
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=[m.strip() for m in settings.cors_allow_methods.split(",") if m.strip()],
    allow_headers=[h.strip() for h in settings.cors_allow_headers.split(",") if h.strip()],
)

# Static (pyVis assets ou quaisquer arquivos)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

# App state (RPC e Cache)
rpc_client = SupabaseRPC(
    supabase_url=settings.supabase_url,
    supabase_key=settings.supabase_key,
    timeout=settings.supabase_timeout,
)
cache = get_cache(settings)

@app.on_event("startup")
async def on_startup():
    await rpc_client.start()
    await cache.start()

@app.on_event("shutdown")
async def on_shutdown():
    await cache.close()
    await rpc_client.close()

@app.get("/health")
async def health():
    """
    Health leve (sem quebrar por atributo inexistente).
    """
    details = {
        "status": "ok",
        "version": "1.0.0",
        "redis_enabled": settings.enable_redis_cache,
        "supabase_url": settings.supabase_url,
    }
    return details

# --------- Endpoints de Grafo ---------

@app.get("/graph/members")
async def graph_members(
    request: Request,
    response: Response,
    p_faccao_id: str = Query(..., description="ID da facção (obrigatório)"),
    p_include_co: Optional[bool] = Query(True, description="Incluir coocorrências relacionadas"),
    p_max_pairs: Optional[int] = Query(500, ge=1, le=5000, description="Limite de pares"),
    preview: Optional[bool] = Query(False, description="Ativar preview truncado"),
    max_nodes: Optional[int] = Query(250, ge=1, le=5000, description="Máximo de nós no preview"),
    max_edges: Optional[int] = Query(500, ge=1, le=20000, description="Máximo de arestas no preview"),
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
):
    """
    Retorna JSON no formato { nodes: [...], edges: [...] } pronto para pyVis/FlutterFlow.
    Busca via Supabase RPC (get_graph_membros) + cache (Redis opcional) + ETag.
    """
    if not p_faccao_id:
        raise HTTPException(status_code=400, detail="p_faccao_id é obrigatório")

    cache_key = f"graph_members:{p_faccao_id}:{p_include_co}:{p_max_pairs}"
    cached = await cache.get(cache_key)
    if cached:
        data = cached
    else:
        payload = {
            "p_faccao_id": p_faccao_id,
            "p_include_co": p_include_co,
            "p_max_pairs": p_max_pairs,
        }
        data = await rpc_client.call(settings.supabase_rpc_fn, payload)
        # Normaliza para o schema esperado do pyVis (id,label,group,title,value / from,to,label,weight)
        data = normalize_graph_schema(data)
        await cache.set(cache_key, data, ttl=settings.cache_api_ttl)

    # Preview (se solicitado)
    if preview:
        data = truncate_preview(data, max_nodes=max_nodes, max_edges=max_edges)

    # ETag / Cache headers HTTP
    body_bytes = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    etag = compute_etag(body_bytes)
    if if_none_match and if_none_match == etag:
        return Response(status_code=304)

    headers = make_cache_headers(settings.cache_api_ttl, etag)
    for k, v in headers.items():
        response.headers[k] = v
    return data

@app.get("/graph/members/html")
async def graph_members_html(
    response: Response,
    p_faccao_id: str = Query(..., description="ID da facção (obrigatório)"),
    p_include_co: Optional[bool] = Query(True),
    p_max_pairs: Optional[int] = Query(500, ge=1, le=5000),
    preview: Optional[bool] = Query(False),
    max_nodes: Optional[int] = Query(250, ge=1, le=5000),
    max_edges: Optional[int] = Query(500, ge=1, le=20000),
    physics: Optional[bool] = Query(True, description="Habilita física no pyVis"),
    height: Optional[str] = Query("650px"),
    width: Optional[str] = Query("100%"),
):
    """
    Retorna uma página HTML contendo o grafo renderizado com pyVis, pronta para embed.
    """
    payload = {
        "p_faccao_id": p_faccao_id,
        "p_include_co": p_include_co,
        "p_max_pairs": p_max_pairs,
    }
    cache_key = f"graph_members:{p_faccao_id}:{p_include_co}:{p_max_pairs}"
    data = await cache.get(cache_key)
    if not data:
        data = await rpc_client.call(settings.supabase_rpc_fn, payload)
        data = normalize_graph_schema(data)
        await cache.set(cache_key, data, ttl=settings.cache_api_ttl)

    if preview:
        data = truncate_preview(data, max_nodes=max_nodes, max_edges=max_edges)

    html = build_pyvis_html(data, physics=physics, height=height, width=width)
    headers = make_cache_headers(settings.cache_api_ttl)
    return HTMLResponse(content=html, headers=headers)

# --------- Documentação OpenAPI YAML estática ---------

@app.get("/openapi.yaml")
async def serve_openapi_yaml():
    """
    Entrega o arquivo docs/openapi.yaml (para Swagger/UIs externas).
    """
    return FileResponse("docs/openapi.yaml", media_type="text/yaml")
