import hashlib
from typing import Dict, Optional

def compute_etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest() + '"'

def make_cache_headers(max_age: int, etag: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Cache-Control": f"public, max-age={max_age}",
    }
    if etag:
        headers["ETag"] = etag
    return headers
