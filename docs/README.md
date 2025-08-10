# Rugs.fun Data Service – Documentation Index

Scope: This service is the authoritative data capture and distribution layer for Rugs.fun. It ingests real-time events from Rugs.fun, validates them against canonical JSON Schemas, persists to MongoDB, and exposes REST + WebSocket APIs for downstream consumers. Advanced analytics are explicitly out-of-scope and should be implemented in a separate consumer application.

Contents
- API
  - api/REST.md – REST endpoints and examples
  - api/WebSocket.md – WebSocket stream and event envelopes
- Schemas
  - schemas/README.md – Schema inventory and validation
- Storage
  - storage/Mongo.md – Collections, fields, indexes, and TTLs
- Operations
  - runbook/Operations.md – Setup, health, logs, monitoring, backups
- Data Quality & PRNG
  - QUALITY.md – Quality flags, God Candles, and PRNG verification

Quick Start
- REST (set BACKEND_URL env to your service URL)
  - Health: curl "$BACKEND_URL/api/health"
  - Readiness: curl "$BACKEND_URL/api/readiness"
  - Metrics: curl "$BACKEND_URL/api/metrics"
  - Games: curl "$BACKEND_URL/api/games?limit=5"
- WebSocket (Node)
  - Example:
    const url = process.env.BACKEND_URL.replace(/^http/, 'ws') + '/api/ws/stream';
    const ws = new (require('ws'))(url);
    ws.on('message', (d) => console.log(d.toString()));

Notes
- Canonical event schemas: docs/ws-schema/*.json
- Ingress requires all backend routes to be prefixed with /api
- This service is a data layer only; consumer apps should subscribe to /api/ws/stream