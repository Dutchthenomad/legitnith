# Rugs.fun Data Service

This repository contains a single-responsibility data service for Rugs.fun.

What it does
- Connects read-only to the Rugs.fun upstream via Socket.IO
- Validates inbound events against canonical JSON Schemas (warn mode)
- Persists to MongoDB with TTL and analysis-friendly indexes
- Exposes REST and WebSocket (/api/ws/stream) for downstream consumers

Key endpoints
- GET /api/health (liveness)
- GET /api/readiness (Mongo ping + upstreamConnected)
- GET /api/metrics (operational counters including schemaValidation, wsSlowClientDrops, dbPingMs)
- GET /api/games, /api/games/current, /api/snapshots, /api/schemas
- WS: /api/ws/stream (normalized frames)

Configuration
- Backend → MongoDB: MONGO_URL
- Database name: DB_NAME
- Upstream URL (optional): RUGS_UPSTREAM_URL (defaults to production URL)
- CORS origins: CORS_ORIGINS
- Do not modify .env protected values; use environment overrides per environment

Operations
- Managed by supervisor; do not run uvicorn directly
- Restart: sudo supervisorctl restart backend
- Logs: tail -n 100 /var/log/supervisor/backend.*.log

Docs
- See docs/ for full API, schemas, storage model, and runbook

License
- Internal use; consult project owner for distribution