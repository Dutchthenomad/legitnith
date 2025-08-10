# Operations Runbook

Environment
- Backend: uses MONGO_URL for DB connection and DB_NAME for database selection
- Frontend: uses REACT_APP_BACKEND_URL for all API calls and WS connections
- Binding: backend listens on 0.0.0.0:8001; all backend routes must use /api prefix

Start/Stop
- Managed by supervisor; do not run uvicorn manually
- Restart commands: sudo supervisorctl restart backend / frontend / all

Health & Monitoring
- GET /api/health for liveness
- GET /api/readiness for readiness (Mongo ping + upstream connection), includes dbOk, dbPingMs
- GET /api/metrics for service stats including schemaValidation counters and wsSlowClientDrops/dbPingMs
- GET /api/connection for upstream Socket.IO status
- Check backend logs: tail -n 100 /var/log/supervisor/backend.*.log

Validation (beta policy)
- Warn mode only: validates inbound events against canonical schemas, tags records, increments counters; data is not dropped
- Observe schemaValidation.total and perEvent counters for anomalies

Backups & Retention
- TTLs: snapshots (10d), events/connection_events (30d)
- Consider periodic offloading of games, trades, god_candles for long-term retention

Upgrades
- Schemas: update docs/ws-schema; service reloads on restart, then verify /api/schemas and validation counters
- Zero-downtime: ensure ingress routing maintains /api prefix and environment variables remain unchanged

Notes
- This service is a data layer only; analytics must be performed by consumer apps subscribing to the data