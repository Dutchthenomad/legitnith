# WebSocket API

Route: /api/ws/stream
- Downstream broadcast channel for normalized live frames
- Message envelope includes a schema version tag and validation summary fields

Message Types
- game_state_update
  - { schema: "v1", type: "game_state_update", gameId, tick, price, phase, validation: { ok, schema }, ts }
- trade
  - { schema: "v1", type: "trade", gameId, playerId, tradeType, tickIndex, amount, qty, price, validation: { ok, schema }, ts }
- side_bet
  - { schema: "v1", type: "side_bet", event, gameId, playerId, validation: { ok, schema }, ts }
- god_candle
  - { schema: "v1", type: "god_candle", gameId, tick, fromPrice, toPrice, ratio, ts }
- rug
  - { schema: "v1", type: "rug", gameId, tick, endPrice, ts }

Notes
- Validation summary fields reflect inbound JSON Schema validation in warn mode (no drops); failures are counted and tagged but not blocked
- Versioning (schema: "v1") is included for forward compatibility
- No inbound messages are expected from consumers; heartbeats are sent from server every ~30s