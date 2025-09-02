import os
from dataclasses import dataclass

@dataclass
class Settings:
    # App
    app_env: str = os.getenv("APP_ENV", "production")
    port: int = int(os.getenv("PORT", "8080"))
    log_level: str = os.getenv("LOG_LEVEL", "info")

    # CORS
    cors_allow_origins: str = os.getenv("CORS_ALLOW_ORIGINS", "*")
    cors_allow_credentials: bool = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"
    cors_allow_headers: str = os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type")
    cors_allow_methods: str = os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS")

    # Supabase
    supabase_url: str = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key: str = os.getenv("SUPABASE_KEY", "")
    supabase_rpc_fn: str = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
    supabase_timeout: float = float(os.getenv("SUPABASE_TIMEOUT", "15"))

    # Cache
    cache_static_max_age: int = int(os.getenv("CACHE_STATIC_MAX_AGE", "86400"))
    cache_api_ttl: int = int(os.getenv("CACHE_API_TTL", "60"))

    # Redis
    enable_redis_cache: bool = os.getenv("ENABLE_REDIS_CACHE", "true").lower() == "true"
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

def get_settings() -> Settings:
    return Settings()
