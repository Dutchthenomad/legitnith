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

References
- Canonical event schemas: docs/ws-schema/*.json
- GRAD_STUDY: see docs/GRAD_STUDY for research background and constraints to follow