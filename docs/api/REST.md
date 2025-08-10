# REST API

Base URL
- Use the frontend environment variable REACT_APP_BACKEND_URL
- All backend routes are prefixed with /api (Ingress rule)

Conventions
- All time fields in responses are ISO8601 strings
- No hardcoded URLs or ports; use environment variables per deployment

Endpoints

GET /api/health
- Returns { status, time }

GET /api/metrics
- Returns operational counters
- { serviceUptimeSec, currentSocketConnected, socketId, lastEventAt, totalMessagesProcessed, totalTrades, totalGamesTracked, messagesPerSecond1m, messagesPerSecond5m, wsSubscribers, errorCounters, schemaValidation }
- schemaValidation: { total, perEvent: { [schemaKey]: { ok, fail } } }

GET /api/connection
- Returns connection state to upstream Rugs.fun (Socket.IO)
- { connected, socket_id, last_event_at, since_connected_ms }

GET /api/live
- Returns current live state snapshot used by HUD

GET /api/snapshots?limit=50
- Returns recent game_state snapshots without full payloads

GET /api/god-candles?gameId=...
- Returns detected God Candle events

GET /api/ohlc?gameId=...&window=5&limit=200
- Returns compacted OHLC indices per 5 ticks

GET /api/games
- Returns recent games with rolling stats and quality flags

GET /api/games/current
- Returns the current active game document

GET /api/games/{game_id}
GET /api/games/{game_id}/quality
- Returns game detail and quality

GET /api/prng/tracking
- Returns PRNG tracking status per game

GET /api/games/{game_id}/verification
POST /api/prng/verify/{game_id}
- Triggers/returns PRNG verification results

GET /api/schemas
- Returns a descriptor list of compiled canonical schemas used for inbound validation
- Each item: { key, id, title, required, properties, outboundType }

Notes
- Persisted resources avoid Mongo ObjectIDs in responses and use UUIDs for WS and persistence convenience
- All endpoints are read-only except POST /api/prng/verify/{game_id}