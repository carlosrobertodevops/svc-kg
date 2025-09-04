APP_ENV=production
PORT=8080
WORKERS=2
LOG_LEVEL=info

CORS_ALLOW_ORIGINS=*
CORS_ALLOW_CREDENTIALS=false
CORS_ALLOW_HEADERS=Authorization,Content-Type
CORS_ALLOW_METHODS=GET,POST,OPTIONS

# SUPABASE (produção / Coolify)
SUPABASE_URL=https://supabase.mondaha.com
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_KEY=
SUPABASE_RPC_FN=get_graph_membros
SUPABASE_TIMEOUT=15

# POSTGRES (local). Se usar, deixe SUPABASE_* em branco.
DATABASE_URL=

CACHE_STATIC_MAX_AGE=86400
CACHE_API_TTL=60

ENABLE_REDIS_CACHE=true
REDIS_URL=redis://redis:6379/0

# <<< Somente se usar docker-compose.coolify-proxy.yml >>>
APP_HOST=svc-kg.mondaha.com
COOLIFY_PROXY_NETWORK=coolify-proxy

# opcional: para teste
SERVER_CMD=gunicorn
