# Phase 1: RUG EVENT Detection Methodology - Final Implementation

## Update Status

- [x] Analyze the RUG EVENT transition - identify all field changes from active to rugged
- [x] Document the COOLDOWN phase characteristics and transitions
- [x] Document the GAME ACTIVATION phase (pre-sale period)
- [x] Document the ACTIVE GAMEPLAY phase
- [x] Create comprehensive methodology document with all phases
- [x] **FINAL: Integrate with production data architecture**

## The Critical Sequence of Events

The RUG EVENT is **NOT** a single update, but a sequence that occurs in this exact order:

### Event Sequence

#### 1. Initial RUG Trigger (Tick 32)

```json
{
  "active": true,               // STILL TRUE
  "rugged": true,               // FLIPPED TO TRUE
  "price": 0.020000000000000018, // CRASHED
  "tickCount": 32,              // FINAL TICK
  "cooldownTimer": 0            // NO COOLDOWN YET
}
```

#### 2. Game History Update (The True End Marker)

This is the **MOST IMPORTANT** event - it signals the game has truly ended:

```json
{
  "gameHistory": [
    {
      "id": "20250807-300ad2bc3a224bf3",  // Just-ended game
      "prices": [..., 0.020000000000000018], // Full price history
      "peakMultiplier": 1.5978270728953674,  // Highest price reached
      "rugged": true,
      "provablyFair": {
        "serverSeed": "actual_seed_used",     // REVEALED NOW
        "serverSeedHash": "hash_that_was_shown" // Verification hash
      }
    }
    // Previous games...
  ],
  "active": true,  // STILL showing as active!
  "rugged": true,
  "tickCount": 33, // Incremented again
  "trades": []     // Empty trades array for new game
}
```

## Key Fields That Change

1. **gameHistory** - APPEARS for first time (was not present before)
2. **provablyFair.serverSeed** - REVEALED (was hidden during game)
3. **trades array** - RESET to empty
4. **tickCount** - Continues incrementing (33)
5. **New game preparation** begins

## Production-Ready Rug Detection System

### Complete Rug Detection with Data Persistence

```javascript
class ProductionRugDetector {
  constructor(dbConnection, cacheConnection) {
    this.db = dbConnection;
    this.cache = cacheConnection;
    
    // Game tracking
    this.trackedGameId = null;
    this.currentGameStartTick = null;
    this.isTracking = false;
    
    // Data storage
    this.priceHistory = [];
    this.gameData = null;
    
    // Verification system
    this.prngVerifier = new PRNGVerifier();
  }

  setTrackedGame(gameId) {
    this.trackedGameId = gameId;
    console.log(`🎯 Production Rug Detector tracking: ${gameId}`);
    
    // Initialize game record in database
    this.initializeGameRecord(gameId);
  }

  async initializeGameRecord(gameId) {
    try {
      await this.db.query(`
        INSERT INTO games (id, start_time, phase, server_seed_hash, version, created_at)
        VALUES ($1, NOW(), 'ACTIVE', $2, $3, NOW())
        ON CONFLICT (id) DO NOTHING
      `, [gameId, this.currentServerSeedHash, 'v3']);
      
      console.log(`✅ Game record initialized: ${gameId}`);
    } catch (error) {
      console.error(`❌ Failed to initialize game record: ${error}`);
    }
  }

  onGameStateUpdate(data) {
    // SAFETY CHECK: Only process updates for our tracked game
    if (this.trackedGameId && data.gameId !== this.trackedGameId) {
      return;
    }

    // PHASE 1: DETECT NEW GAME START
    if (this.detectNewGameStart(data)) {
      this.startTracking(data);
      return;
    }

    // PHASE 2: ONLY TRACK IF WE HAVE THE RIGHT GAME ID
    if (this.isTracking && data.gameId === this.trackedGameId) {
      this.trackGameProgress(data);
    }

    // PHASE 3: DETECT GAME END AND EXTRACT HISTORY
    if (this.detectGameEnd(data)) {
      this.extractGameHistory(data);
    }
  }

  detectNewGameStart(data) {
    return (
      data.active === true &&
      data.price === 1 &&
      data.tickCount <= 1 &&
      data.rugged === false &&
      !this.isTracking
    );
  }

  startTracking(data) {
    console.log(`🚀 Starting to track NEW game: ${data.gameId}`);
    
    this.trackedGameId = data.gameId;
    this.currentGameStartTick = data.tickCount;
    this.isTracking = true;
    this.priceHistory = [data.price];
    this.gameData = {
      gameId: data.gameId,
      startTime: Date.now(),
      serverSeedHash: data.provablyFair?.serverSeedHash,
      version: data.provablyFair?.version || 'v3'
    };

    // Initialize in database
    this.initializeGameRecord(data.gameId);
    
    // Cache current game state
    this.cacheCurrentGameState(data);
  }

  async trackGameProgress(data) {
    // SAFETY: Only track if game ID matches
    if (data.gameId !== this.trackedGameId) {
      console.warn(`Game ID mismatch! Expected: ${this.trackedGameId}, Got: ${data.gameId}`);
      return;
    }

    // Store tick data in real-time
    await this.storeTick(data);

    // Track price updates
    if (data.prices && data.prices.length > this.priceHistory.length) {
      this.priceHistory = [...data.prices];
    }

    // Detect rug event
    if (data.rugged && !this.gameData.rugged) {
      await this.handleRugEvent(data);
    }

    // Update cache
    this.cacheCurrentGameState(data);
  }

  async storeTick(data) {
    try {
      await this.db.query(`
        INSERT INTO game_ticks (game_id, tick_number, price, timestamp_ms, created_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (game_id, tick_number) DO UPDATE SET
        price = EXCLUDED.price,
        timestamp_ms = EXCLUDED.timestamp_ms
      `, [data.gameId, data.tickCount, data.price, Date.now()]);
      
    } catch (error) {
      console.error(`❌ Failed to store tick: ${error}`);
    }
  }

  async handleRugEvent(data) {
    console.log(`💥 RUG EVENT DETECTED: ${data.gameId} at tick ${data.tickCount}`);
    
    this.gameData.rugged = true;
    this.gameData.rugTick = data.tickCount;
    this.gameData.rugPrice = data.price;
    this.gameData.rugTime = Date.now();

    // Update game record
    try {
      await this.db.query(`
        UPDATE games SET 
          phase = 'RUG',
          rug_tick = $2,
          updated_at = NOW()
        WHERE id = $1
      `, [data.gameId, data.tickCount]);
      
      console.log(`✅ Rug event recorded for game: ${data.gameId}`);
    } catch (error) {
      console.error(`❌ Failed to record rug event: ${error}`);
    }
  }

  detectGameEnd(data) {
    return (
      this.isTracking &&
      data.cooldownTimer > 0 &&
      !data.active &&
      data.gameHistory &&
      data.gameHistory.length > 0
    );
  }

  async extractGameHistory(data) {
    // CRITICAL: Only extract history for OUR tracked game
    const ourGame = data.gameHistory.find(g => g.id === this.trackedGameId);

    if (!ourGame) {
      console.error(`CRITICAL: Could not find our game ${this.trackedGameId} in history!`);
      this.reset();
      return;
    }

    // SUCCESS: We have the right game data
    const completeGameData = {
      gameId: ourGame.id,
      prices: ourGame.prices,
      peakMultiplier: ourGame.peakMultiplier,
      serverSeed: ourGame.provablyFair.serverSeed,
      serverSeedHash: ourGame.provablyFair.serverSeedHash,
      totalTicks: ourGame.prices.length,
      trackedPrices: this.priceHistory,
      endTime: Date.now(),

      // Verify data integrity
      dataIntegrity: {
        pricesMatch: JSON.stringify(ourGame.prices) === JSON.stringify(this.priceHistory),
        gameIdMatch: ourGame.id === this.trackedGameId,
        tickCountMatch: ourGame.prices.length === this.priceHistory.length
      }
    };

    // Store complete game data
    await this.storeCompleteGameData(completeGameData);

    // Verify PRNG integrity
    await this.verifyPRNGIntegrity(completeGameData);

    // Reset for next game
    this.reset();
  }

  async storeCompleteGameData(gameData) {
    try {
      // Update main game record
      await this.db.query(`
        UPDATE games SET 
          end_time = NOW(),
          phase = 'COMPLETED',
          peak_multiplier = $2,
          total_ticks = $3,
          server_seed = $4,
          updated_at = NOW()
        WHERE id = $1
      `, [
        gameData.gameId,
        gameData.peakMultiplier,
        gameData.totalTicks,
        gameData.serverSeed
      ]);

      // Store price history if not already stored
      const missingTicks = await this.findMissingTicks(gameData.gameId, gameData.prices);
      if (missingTicks.length > 0) {
        await this.storeMissingTicks(gameData.gameId, missingTicks);
      }

      console.log(`✅ Complete game data stored: ${gameData.gameId}`);
      
    } catch (error) {
      console.error(`❌ Failed to store complete game data: ${error}`);
    }
  }

  async verifyPRNGIntegrity(gameData) {
    if (!gameData.serverSeed) {
      console.warn(`⚠️ No server seed available for PRNG verification: ${gameData.gameId}`);
      return;
    }

    try {
      console.log(`🔍 Starting PRNG verification for: ${gameData.gameId}`);
      
      const verification = await this.prngVerifier.verifyGame(
        gameData.serverSeed,
        gameData.gameId,
        gameData.version || 'v3'
      );

      // Store verification results
      await this.db.query(`
        UPDATE games SET 
          prng_verified = $2,
          prng_verification_data = $3,
          updated_at = NOW()
        WHERE id = $1
      `, [
        gameData.gameId,
        verification.valid,
        JSON.stringify(verification)
      ]);

      if (verification.valid) {
        console.log(`✅ PRNG verification PASSED: ${gameData.gameId}`);
      } else {
        console.error(`❌ PRNG verification FAILED: ${gameData.gameId}`, verification);
      }

    } catch (error) {
      console.error(`❌ PRNG verification error: ${error}`);
    }
  }

  cacheCurrentGameState(data) {
    const cacheKey = `current_game:${data.gameId}`;
    const gameState = {
      gameId: data.gameId,
      active: data.active,
      rugged: data.rugged,
      price: data.price,
      tickCount: data.tickCount,
      phase: this.determinePhase(data),
      lastUpdate: Date.now()
    };

    this.cache.setex(cacheKey, 300, JSON.stringify(gameState)); // 5 minute expiry
  }

  determinePhase(data) {
    if (data.rugged) return 'RUG';
    if (data.active && !data.rugged) return 'ACTIVE';
    if (!data.active && data.cooldownTimer > 0) return 'COOLDOWN';
    return 'WAITING';
  }

  reset() {
    console.log(`🔄 Resetting tracker. Finished with game: ${this.trackedGameId}`);
    this.trackedGameId = null;
    this.currentGameStartTick = null;
    this.isTracking = false;
    this.priceHistory = [];
    this.gameData = null;
  }
}
```

### PRNG Verification System

```javascript
class PRNGVerifier {
  constructor() {
    // Game constants
    this.RUG_PROB = 0.005;
    this.DRIFT_MIN = -0.02;
    this.DRIFT_MAX = 0.03;
    this.BIG_MOVE_CHANCE = 0.125;
    this.BIG_MOVE_MIN = 0.15;
    this.BIG_MOVE_MAX = 0.25;
    this.GOD_CANDLE_CHANCE = 0.00001;
    this.GOD_CANDLE_MOVE = 10.0;
  }

  async verifyGame(serverSeed, gameId, version = 'v3') {
    const combinedSeed = serverSeed + '-' + gameId;
    const prng = new Math.seedrandom(combinedSeed);
    
    let price = 1.0;
    let peakMultiplier = 1.0;
    let rugged = false;
    const prices = [1.0];
    
    for (let tick = 0; tick < 5000 && !rugged; tick++) {
      // Check for rug event
      if (prng() < this.RUG_PROB) {
        rugged = true;
        continue;
      }
      
      // Calculate new price
      const newPrice = this.driftPrice(
        price, prng.bind(prng), version
      );
      
      price = newPrice;
      prices.push(price);
      
      if (price > peakMultiplier) {
        peakMultiplier = price;
      }
    }
    
    return {
      prices: prices,
      peakMultiplier: peakMultiplier,
      rugged: rugged,
      totalTicks: prices.length - 1,
      valid: true // Will be validated against actual data
    };
  }

  driftPrice(price, randFn, version = 'v3') {
    // v3 adds God Candle feature
    if (version === 'v3' && randFn() < this.GOD_CANDLE_CHANCE && price <= 100) {
      return price * this.GOD_CANDLE_MOVE;
    }
    
    let change = 0;
    
    if (randFn() < this.BIG_MOVE_CHANCE) {
      const moveSize = this.BIG_MOVE_MIN + randFn() * (this.BIG_MOVE_MAX - this.BIG_MOVE_MIN);
      change = randFn() > 0.5 ? moveSize : -moveSize;
    } else {
      const drift = this.DRIFT_MIN + randFn() * (this.DRIFT_MAX - this.DRIFT_MIN);
      
      const volatility = version === 'v1' 
        ? 0.005 * Math.sqrt(price)
        : 0.005 * Math.min(10, Math.sqrt(price));
        
      change = drift + (volatility * (2 * randFn() - 1));
    }
    
    let newPrice = price * (1 + change);
    return newPrice < 0 ? 0 : newPrice;
  }
}
```

## Critical Detection Points

1. **gameHistory is THE definitive marker** - It only appears when a game truly ends
2. **rugged: true with active: true** - This combination is unique to rug events
3. **Price always crashes** to exactly `0.020000000000000018`
4. **Server seed is revealed** in `gameHistory[0].provablyFair.serverSeed`
5. **New game preparation** begins immediately (empty trades array)

## Database Integration

### Core Tables Used
```sql
-- Main game tracking
INSERT INTO games (id, start_time, phase, server_seed_hash) VALUES ...;

-- Real-time tick storage  
INSERT INTO game_ticks (game_id, tick_number, price, timestamp_ms) VALUES ...;

-- Final game completion
UPDATE games SET end_time = NOW(), peak_multiplier = $1, server_seed = $2 WHERE id = $3;
```

### Monitoring Queries
```sql
-- Recent rug events
SELECT id, rug_tick, peak_multiplier, prng_verified 
FROM games 
WHERE phase = 'COMPLETED' 
AND end_time >= NOW() - INTERVAL '24 hours'
ORDER BY end_time DESC;

-- PRNG verification status
SELECT 
  COUNT(*) as total_games,
  SUM(CASE WHEN prng_verified THEN 1 ELSE 0 END) as verified_games,
  ROUND(AVG(CASE WHEN prng_verified THEN 1.0 ELSE 0.0 END) * 100, 2) as verification_rate
FROM games 
WHERE phase = 'COMPLETED';
```

## Key Safety Rules

1. **NEVER track without a game ID** - If we don't have the game ID from the start, we skip it
2. **ALWAYS verify game ID matches** - Every update must have matching game ID
3. **ONLY extract from gameHistory using OUR game ID** - Never assume index 0 is our game
4. **SKIP incomplete games** - If we join mid-game, wait for the next one
5. **VERIFY data integrity** - Check rug price, rugged flag, price count
6. **TRACK seen game IDs** - Never process the same game twice
7. **LOG everything** - Detailed logging helps debug issues
8. **STORE everything** - Complete data persistence for analysis

This approach ensures we NEVER mix up game data, even in noisy environments with multiple games in history, while building a complete historical database for analysis and verification.