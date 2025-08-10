# Data Quality & PRNG Verification

Quality Flags (games.quality)
- duplicateOrOutOfOrder: non-increasing tick index detected
- largeGap: tick jump larger than 10
- priceNonPositive: price <= 0 seen
- lastCheckedAt: ISO timestamp when checks last ran

God Candle Detection
- Detected when price jumps 10x within a tick (with guard against >100x price cap prior to jump)
- Persisted to god_candles with ratio and underCap flag; games updated with hasGodCandle and related fields

PRNG Verification
- run_prng_verification combines serverSeed and gameId into Alea PRNG
- Compares generated price series and peakMultiplier to expected arrays captured at end of game
- Results persisted to prng_tracking and games.prngVerificationData; games.prngVerified set accordingly
- Statuses: TRACKING → COMPLETE → VERIFIED/FAILED or AWAITING_SEED/MISSING_EXPECTED

Validation Policy
- Warn mode: no data drops; records are tagged with validation summary and counters are exposed under /api/metrics
- Consumer apps may choose to filter only validation.ok events if their use case requires strict schema conformance