import httpx
from typing import Any, Dict


class SupabaseRPC:
    def __init__(self, supabase_url: str, supabase_key: str, timeout: float = 15.0):
        self._base = f"{supabase_url}/rest/v1"
        self._key = supabase_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def start(self):
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(base_url=self._base, headers=headers, timeout=self._timeout)

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def call(self, fn_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._client:
            raise RuntimeError("SupabaseRPC client not started")
        url = f"/rpc/{fn_name}"
        r = await self._client.post(url, json=payload)
        r.raise_for_status()
        return r.json()
