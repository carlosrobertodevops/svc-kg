version: "3.9"

services:
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    networks: [proxy]

  svc-kg:
    build:
      context: .
      dockerfile: Dockerfile
    image: svc-kg:latest
    env_file:
      - .env
    environment:
      APP_ENV: production
      PORT: 8080
      WORKERS: 2
      LOG_LEVEL: info

      CORS_ALLOW_ORIGINS: "*"
      CORS_ALLOW_CREDENTIALS: "false"
      CORS_ALLOW_HEADERS: "Authorization,Content-Type"
      CORS_ALLOW_METHODS: "GET,POST,OPTIONS"

      SUPABASE_URL: "https://supabase.mondaha.com"
      SUPABASE_ANON_KEY: "${SUPABASE_ANON_KEY}"
      SUPABASE_SERVICE_KEY: "${SUPABASE_SERVICE_KEY}"
      SUPABASE_RPC_FN: "get_graph_membros"
      SUPABASE_TIMEOUT: 15

      CACHE_STATIC_MAX_AGE: 86400
      CACHE_API_TTL: 60

      ENABLE_REDIS_CACHE: "true"
      REDIS_URL: "redis://redis:6379/0"

      SERVER_CMD: gunicorn
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

    # <<< LABELS fixas, sem variáveis >>>
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.svckg.rule=Host(`svc-kg.mondaha.com`)"
      - "traefik.http.routers.svckg.entrypoints=websecure"
      - "traefik.http.routers.svckg.tls=true"
      - "traefik.http.services.svckg.loadbalancer.server.port=8080"

    networks: [proxy]

networks:
  proxy:
    external: true
    # coloque aqui o **nome EXATO** da rede proxy do Coolify/Traefik
    name: coolify-proxy
