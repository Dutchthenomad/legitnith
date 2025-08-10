# Canonical JSON Schemas & Validation

Location: docs/ws-schema/*.json

Overview
- The backend loads and compiles schemas at startup and validates inbound events in warn mode
- Validation stats are exposed under /api/metrics.schemaValidation and per-record tags

Schemas (key → file)
- gameStateUpdate → gameStateUpdate.json
- newTrade → newTrade.json
- currentSideBet → currentSideBet.json
- newSideBet → newSideBet.json
- gameStatePlayerUpdate → gameStatePlayerUpdate.json
- playerUpdate → playerUpdate.json
- shared definitions → shared.defs.json

Inbound event routing
- gameStateUpdate → gameStateUpdate
- standard/newTrade → newTrade
- standard/sideBetPlaced → currentSideBet
- sideBet, standard/sideBetResult → newSideBet
- gameStatePlayerUpdate → gameStatePlayerUpdate
- playerUpdate → playerUpdate

Outbound mapping (for HUD filtering)
- game_state_update → gameStateUpdate
- trade → newTrade
- side_bet → currentSideBet/newSideBet
- game_state_player_update → gameStatePlayerUpdate
- player_update → playerUpdate

Record tagging
- Stored docs (snapshots, trades, side_bets, events) include { validation: { ok, schema, error? } } when applicable

Policy
- Warn mode: no data drops; invalid payloads are logged via counters and tagged for forensics
- Future options (not enabled): drop-on-fail, or storeRaw+flag