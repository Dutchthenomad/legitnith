# MongoDB Storage Model

Database: from env DB_NAME (backend uses MONGO_URL)

Collections & Indexes
- game_state_snapshots
  - Fields: _id (uuid), gameId, tickCount, active, rugged, price, cooldownTimer, provablyFair, phase, payload, validation?, createdAt
  - Indexes: (gameId, tickCount), createdAt (TTL 10d)
- trades
  - Fields: _id (uuid), eventId, gameId, playerId, type, qty, tickIndex, coin, amount, price, validation?, createdAt
  - Indexes: (gameId, tickIndex), eventId (unique for idempotency)
- games
  - Fields: id, phase, version, serverSeedHash, lastSeenAt, startTime, endTime, rugTick, endPrice, peakMultiplier, totalTicks, hasGodCandle, prngVerified, prngVerificationData, quality, history, createdAt, updatedAt
  - Indexes: id (unique), phase, hasGodCandle, prngVerified, startTime, endTime, rugTick, endPrice, peakMultiplier, totalTicks
- events
  - Fields: _id (uuid), type, payload, validation?, createdAt (TTL 30d)
  - Indexes: (type, createdAt), createdAt TTL 30d
- connection_events
  - Fields: _id (uuid), socketId, eventType, metadata, timestampMs, createdAt (TTL 30d)
  - Indexes: (eventType, createdAt), createdAt TTL 30d
- prng_tracking
  - Fields: gameId, serverSeedHash, serverSeed?, version, status, verification, createdAt, updatedAt
  - Indexes: gameId (unique)
- god_candles
  - Fields: _id (uuid), gameId, tickIndex, fromPrice, toPrice, ratio, version, underCap, createdAt
  - Indexes: (gameId, tickIndex) unique, createdAt, underCap
- game_ticks
  - Fields: _id (uuid), gameId, tick, price, createdAt, updatedAt
  - Indexes: (gameId, tick) unique
- game_indices (5-tick OHLC)
- side_bets
  - Fields: _id (uuid), event, gameId, playerId, startTick?, endTick?, betAmount?, targetSeconds?, payoutRatio?, won?, pnl?, xPayout?, payload, validation?, createdAt
  - Indexes: (gameId, createdAt desc), optional (gameId, startTick)
- meta (KV store)
  - Fields: key, value?, plus dynamic fields depending on key (e.g., live_state)
  - Indexes: key (unique)
- status_checks
  - Fields: id (uuid), client_name, timestamp
  - Indexes: timestamp desc

  - Fields: _id (uuid), gameId, index, startTick, endTick, open, high, low, close, createdAt, updatedAt
  - Indexes: (gameId, index) unique, updatedAt

Notes
- UUIDs are used for primary keys instead of Mongo ObjectIDs to simplify downstream JSON handling
- TTL values may be adjusted in production; the service attempts collMod if index exists