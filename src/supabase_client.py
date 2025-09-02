# src/supabase_client.py
import httpx
from fastapi import HTTPException
from typing import Any, Dict, Tuple
from src.settings import settings

class SupabaseRPC:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0, read=10.0),
            limits=httpx.Limits(max_keepalive_connections=100, max_connections=200),
            headers={
                "apikey": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def call_rpc(self, func: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.base_url or not self.api_key:
            raise HTTPException(status_code=500, detail="Supabase URL/KEY não configurados")
        url = f"{self.base_url}/rest/v1/rpc/{func}"
        try:
            r = await self.client.post(url, json=payload)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Erro ao conectar Supabase: {e}") from e

        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        try:
            return r.json()
        except ValueError:
            return {"raw": r.text}

    async def ping(self) -> Tuple[bool, str]:
        # chamada leve; qualquer resposta HTTP já valida conectividade
        if not self.base_url:
            return False, "SUPABASE_URL vazio"
        url = f"{self.base_url}/rest/v1/"
        try:
            r = await self.client.get(url)
            return True, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)

rpc_client = SupabaseRPC(settings.supabase_url, settings.supabase_key)
