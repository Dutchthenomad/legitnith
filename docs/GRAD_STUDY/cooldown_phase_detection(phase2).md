# PHASE 2: COOLDOWN - Post-Rug State Transition

## The Key Transition Moment

The first appearance of `"cooldownTimer": 14900` or `"cooldownTimer": 15000` marks the **TRUE beginning** of the cooldown phase.

## Complete Sequence of COOLDOWN Entry

### 1. First Cooldown Update (Initial Timer)

```json
{
  "active": false,                // NOW FALSE!
  "price": 1,                     // RESET TO 1
  "prices": [1],                  // RESET TO SINGLE ELEMENT
  "rugged": false,                // RESET TO FALSE
  "tickCount": 0,                 // RESET TO 0
  "cooldownTimer": 15000,         // STARTS AT 15000ms (15 seconds)
  "gameId": "20250807-f4b38de436964086",  // NEW GAME ID!
  "trades": [],                   // EMPTY
  "gameHistory": [                // CONTAINS ENDED GAME
    {
      "id": "20250807-300ad2bc3a224bf3",  // JUST-ENDED GAME
      "prices": [...],                    // FULL PRICE HISTORY
      "peakMultiplier": 1.5978270728953674,
      "provablyFair": {
        "serverSeed": "c7e8a226ffe83633d6bb07db7465d33e45f0d73c4716e54406c0dbc086a1f129",
        "serverSeedHash": "b1abfe83084b39218f43c06e4a306ee0d33c4961e09b959a8a39c6e85265c2b5"
      }
    }
    // Plus previous games...
  ]
}
```

### 2. Slightly Later Update (Timer Counting Down)

```json
{
  "cooldownTimer": 14900,  // 100ms later
  // All other fields remain the same
}
```

## Detection and Logging Methods

### Method 1: Complete Game History Logging (UPDATED FOR SAFETY)

```javascript
class SafeCooldownPhaseLogger {
  constructor() {
    this.trackedGameId = null;
  }

  setTrackedGame(gameId) {
    this.trackedGameId = gameId;
  }

  onCooldownStart(data) {
    // CRITICAL: Only process if we have a tracked game ID
    if (!this.trackedGameId) {
      console.log('❌ No tracked game ID - SKIPPING cooldown processing');
      return;
    }

    // Detect cooldown start
    if (data.cooldownTimer > 0 && !data.active && data.gameHistory) {
      
      // SAFETY: Find OUR specific game in history
      const ourGame = data.gameHistory.find(g => g.id === this.trackedGameId);
      
      if (!ourGame) {
        console.error(`❌ CRITICAL: Could not find our game ${this.trackedGameId} in history!`);
        this.trackedGameId = null;
        return;
      }

      const logEntry = {
        phase: 'COOLDOWN_START',
        timestamp: Date.now(),

        // Current state
        cooldownTimer: data.cooldownTimer,
        nextGameId: data.gameId,

        // VERIFIED game data for OUR tracked game
        endedGame: {
          id: ourGame.id,
          prices: ourGame.prices,
          peakMultiplier: ourGame.peakMultiplier,
          totalTicks: ourGame.prices.length,

          // PRNG Data - Critical for analysis
          serverSeed: ourGame.provablyFair.serverSeed,
          serverSeedHash: ourGame.provablyFair.serverSeedHash,

          // Calculate game metrics
          startPrice: ourGame.prices[0],
          endPrice: ourGame.prices[ourGame.prices.length - 1],
          rugTick: ourGame.prices.length - 1,
          gameVersion: ourGame.gameVersion
        },

        // New game preparation
        nextGame: {
          id: data.gameId,
          serverSeedHash: data.provablyFair?.serverSeedHash,
          version: data.provablyFair?.version
        },

        // Additional metadata
        rugpool: data.rugpool,
        connectedPlayers: data.connectedPlayers,
        leaderboard: data.leaderboard,

        // Data verification
        dataIntegrity: {
          gameIdMatch: ourGame.id === this.trackedGameId,
          hasServerSeed: !!ourGame.provablyFair?.serverSeed,
          validRugPrice: Math.abs(ourGame.prices[ourGame.prices.length - 1] - 0.020000000000000018) < 0.001
        }
      };

      // Only save if data integrity checks pass
      if (logEntry.dataIntegrity.gameIdMatch && 
          logEntry.dataIntegrity.hasServerSeed && 
          logEntry.dataIntegrity.validRugPrice) {
        this.saveGameHistory(logEntry);
        console.log('✅ VERIFIED game data saved for:', this.trackedGameId);
      } else {
        console.error('❌ Data integrity check FAILED for game:', this.trackedGameId);
      }

      // Reset for next game
      this.trackedGameId = null;
    }
  }

  saveGameHistory(logEntry) {
    // Your storage implementation
  }
}
```

### Method 2: Safe Price History Extraction (UPDATED)

```javascript
function extractPriceHistorySafe(gameHistory, trackedGameId) {
  // CRITICAL: Never extract without verified game ID
  if (!trackedGameId) {
    console.error('❌ No tracked game ID provided - cannot safely extract price history');
    return null;
  }

  if (!gameHistory || !Array.isArray(gameHistory)) {
    console.error('❌ Invalid game history provided');
    return null;
  }

  // Find OUR specific tracked game
  const ourGame = gameHistory.find(g => g.id === trackedGameId);

  if (!ourGame) {
    console.error(`❌ Tracked game ${trackedGameId} not found in history`);
    // Log available IDs for debugging
    console.error('Available game IDs:', gameHistory.map(g => g.id));
    return null;
  }

  // Verify price data integrity
  if (!ourGame.prices || !Array.isArray(ourGame.prices) || ourGame.prices.length === 0) {
    console.error(`❌ Invalid price data for game ${trackedGameId}`);
    return null;
  }

  return {
    gameId: ourGame.id,
    prices: ourGame.prices,
    priceCount: ourGame.prices.length,
    peak: Math.max(...ourGame.prices),
    rugPrice: ourGame.prices[ourGame.prices.length - 1],
    serverSeed: ourGame.provablyFair?.serverSeed,
    serverSeedHash: ourGame.provablyFair?.serverSeedHash,
    
    // Data verification
    isValidData: {
      hasPrices: ourGame.prices.length > 0,
      hasServerSeed: !!ourGame.provablyFair?.serverSeed,
      correctRugPrice: Math.abs(ourGame.prices[ourGame.prices.length - 1] - 0.020000000000000018) < 0.001,
      gameMatches: ourGame.id === trackedGameId
    }
  };
}
```

### Method 3: Safe PRNG Data Extraction (UPDATED)

```javascript
function extractPRNGDataSafe(data, trackedGameId) {
  // CRITICAL: Only extract for verified tracked games
  if (!trackedGameId) {
    console.error('❌ No tracked game ID - cannot extract PRNG data safely');
    return null;
  }

  if (!data.gameHistory || !Array.isArray(data.gameHistory)) {
    console.error('❌ No valid game history for PRNG extraction');
    return null;
  }

  // Find OUR specific game
  const ourGame = data.gameHistory.find(g => g.id === trackedGameId);

  if (!ourGame) {
    console.error(`❌ Tracked game ${trackedGameId} not found for PRNG extraction`);
    return null;
  }

  // Verify PRNG data completeness
  if (!ourGame.provablyFair?.serverSeed) {
    console.error(`❌ Server seed not revealed for game ${trackedGameId}`);
    return null;
  }

  return {
    // Ended game PRNG (VERIFIED)
    endedGame: {
      id: ourGame.id,
      serverSeed: ourGame.provablyFair.serverSeed,  // NOW REVEALED!
      serverSeedHash: ourGame.provablyFair.serverSeedHash,
      gameVersion: ourGame.gameVersion,

      // Parse timestamp from game ID
      timestamp: parseGameIdToTimestamp(ourGame.id)
    },

    // Next game PRNG (seed not yet revealed)
    nextGame: {
      id: data.gameId,
      serverSeedHash: data.provablyFair?.serverSeedHash,  // Hash only
      version: data.provablyFair?.version
    },

    // Data verification
    verification: {
      gameIdMatch: ourGame.id === trackedGameId,
      serverSeedRevealed: !!ourGame.provablyFair?.serverSeed,
      hasNextGameData: !!data.gameId,
      extractionTime: Date.now()
    }
  };
}

function parseGameIdToTimestamp(gameId) {
  // Format: YYYYMMDD-hexstring
  const [datePart, hexPart] = gameId.split('-');

  const year = datePart.substring(0, 4);
  const month = datePart.substring(4, 6);
  const day = datePart.substring(6, 8);

  // The hex part is likely Unix timestamp or random
  return {
    date: `${year}-${month}-${day}`,
    uniqueId: hexPart,
    fullId: gameId
  };
}
```

### Method 4: Complete State Transition Logger

```javascript
class GameTransitionLogger {
  constructor() {
    this.lastState = null;
    this.transitionLog = [];
  }

  logTransition(currentState) {
    if (!this.lastState) {
      this.lastState = currentState;
      return;
    }

    // Detect RUG → COOLDOWN transition
    if (this.lastState.rugged &&
        !currentState.active &&
        currentState.cooldownTimer > 0) {

      this.transitionLog.push({
        type: 'RUG_TO_COOLDOWN',
        timestamp: Date.now(),

        // State before (rug moment)
        rugState: {
          gameId: this.lastState.gameId,
          finalPrice: this.lastState.price,
          tickCount: this.lastState.tickCount
        },

        // State after (cooldown start)
        cooldownState: {
          newGameId: currentState.gameId,
          cooldownTimer: currentState.cooldownTimer,
          gameHistoryAvailable: !!currentState.gameHistory
        },

        // Critical data from history
        gameData: this.extractGameData(currentState.gameHistory)
      });
    }

    this.lastState = currentState;
  }

  extractGameData(gameHistory) {
    if (!gameHistory || !gameHistory[0]) return null;

    const game = gameHistory[0];
    return {
      id: game.id,
      priceHistory: game.prices,
      peakMultiplier: game.peakMultiplier,
      serverSeed: game.provablyFair.serverSeed,
      serverSeedHash: game.provablyFair.serverSeedHash,
      totalTicks: game.prices.length
    };
  }
}
```

## Key Fields That Define COOLDOWN Phase

1. **`active: false`** - Game is NOT active
2. **`cooldownTimer > 0`** - Timer is counting down (starts at 15000ms)
3. **`price: 1`** - Reset to starting price
4. **`prices: [1]`** - Reset to single element array
5. **`rugged: false`** - Reset for new game
6. **`tickCount: 0`** - Reset for new game
7. **`gameId`** - NEW game ID already assigned
8. **`gameHistory[0]`** - Contains the just-ended game with full data
9. **`provablyFair.serverSeed`** - REVEALED in gameHistory for verification
10. **`provablyFair.serverSeedHash`** - NEW hash for upcoming game

## Critical Data Points to Log

### 1. Just-Ended Game
- Full price history array
- Server seed (for PRNG verification)
- Peak multiplier achieved
- Game ID for reference

### 2. New Game Preparation
- New game ID (already generated)
- New server seed hash (seed hidden)
- Initial countdown timer value

### 3. Player/System State
- Final leaderboard
- Rugpool data and winners
- Connected players count

## **CRITICAL: Safe Game ID Tracking for COOLDOWN Phase**

### The Golden Rule: Never Extract History Without Verified Game ID

**ABSOLUTELY CRITICAL:** During cooldown phase, we must ONLY extract game history for games we tracked from start to finish. Here's the safe methodology:

```javascript
class SafeCooldownTracker {
  constructor() {
    this.trackedGameId = null;
    this.gameStartTimestamp = null;
    this.isGameComplete = false;
  }

  onCooldownStart(data) {
    // CRITICAL: Only process if this was OUR tracked game
    if (!this.trackedGameId || !data.gameHistory) {
      console.log('No tracked game or no history - SKIPPING cooldown processing');
      return;
    }

    // Find OUR specific game in the history
    const ourGame = data.gameHistory.find(g => g.id === this.trackedGameId);

    if (!ourGame) {
      console.error(`CRITICAL: Could not find our tracked game ${this.trackedGameId} in history!`);
      this.reset();
      return;
    }

    // SUCCESS: We can safely extract our game's data
    const verifiedGameData = {
      // Tracked game metadata
      gameId: this.trackedGameId,
      trackedFrom: this.gameStartTimestamp,
      completedAt: Date.now(),

      // Verified game data from history
      prices: ourGame.prices,
      peakMultiplier: ourGame.peakMultiplier,
      serverSeed: ourGame.provablyFair.serverSeed,
      serverSeedHash: ourGame.provablyFair.serverSeedHash,
      totalTicks: ourGame.prices.length,

      // Cooldown state data
      nextGameId: data.gameId,
      cooldownTimer: data.cooldownTimer,
      rugpool: data.rugpool,
      leaderboard: data.leaderboard,

      // Data integrity verification
      integrity: this.verifyDataIntegrity(ourGame)
    };

    this.processVerifiedGameData(verifiedGameData);
    this.reset();
  }

  verifyDataIntegrity(gameData) {
    const checks = {
      hasServerSeed: !!gameData.provablyFair?.serverSeed,
      hasPriceHistory: Array.isArray(gameData.prices) && gameData.prices.length > 0,
      properRugEnding: this.checkRugEnding(gameData.prices),
      isMarkedRugged: gameData.rugged === true,
      hasValidPeakMultiplier: typeof gameData.peakMultiplier === 'number' && gameData.peakMultiplier >= 1
    };

    const allChecksPass = Object.values(checks).every(check => check === true);
    
    return {
      ...checks,
      allChecksPass,
      gameId: gameData.id
    };
  }

  checkRugEnding(prices) {
    if (!prices || prices.length === 0) return false;
    const lastPrice = prices[prices.length - 1];
    // Check if last price is the rug price (with tolerance)
    return Math.abs(lastPrice - 0.020000000000000018) < 0.001;
  }

  processVerifiedGameData(gameData) {
    if (!gameData.integrity.allChecksPass) {
      console.error('❌ Data integrity check FAILED:', gameData.integrity);
      return;
    }

    console.log('✅ VERIFIED COOLDOWN DATA:', {
      gameId: gameData.gameId,
      ticks: gameData.totalTicks,
      peak: gameData.peakMultiplier,
      serverSeed: gameData.serverSeed.substring(0, 8) + '...'
    });

    // Safe to process this verified data
    this.saveCooldownData(gameData);
  }

  // Called when a new game starts
  startTrackingGame(gameId) {
    this.trackedGameId = gameId;
    this.gameStartTimestamp = Date.now();
    this.isGameComplete = false;
    console.log(`📊 Started tracking game: ${gameId}`);
  }

  // Called when game ends (rug detected)
  markGameComplete() {
    this.isGameComplete = true;
    console.log(`🏁 Game ${this.trackedGameId} marked as complete`);
  }

  reset() {
    console.log(`🔄 Resetting cooldown tracker (was tracking: ${this.trackedGameId})`);
    this.trackedGameId = null;
    this.gameStartTimestamp = null;
    this.isGameComplete = false;
  }

  saveCooldownData(gameData) {
    // Your storage implementation
  }
}
```

### Enhanced Safe Price History Extraction

```javascript
function extractPriceHistorySafe(gameHistory, trackedGameId) {
  // NEVER extract without a verified game ID
  if (!trackedGameId) {
    console.error('❌ No tracked game ID - cannot safely extract price history');
    return null;
  }

  if (!gameHistory || !Array.isArray(gameHistory)) {
    console.error('❌ Invalid or missing game history');
    return null;
  }

  // Find OUR specific game
  const ourGame = gameHistory.find(g => g.id === trackedGameId);

  if (!ourGame) {
    console.error(`❌ Tracked game ${trackedGameId} not found in history`);
    // List available game IDs for debugging
    const availableIds = gameHistory.map(g => g.id);
    console.error('Available game IDs:', availableIds);
    return null;
  }

  // Verify the game data is complete
  if (!ourGame.prices || !Array.isArray(ourGame.prices)) {
    console.error(`❌ Game ${trackedGameId} has invalid price data`);
    return null;
  }

  // SUCCESS: Return verified data
  return {
    gameId: ourGame.id,
    prices: ourGame.prices,
    priceCount: ourGame.prices.length,
    peak: Math.max(...ourGame.prices),
    rugPrice: ourGame.prices[ourGame.prices.length - 1],
    serverSeed: ourGame.provablyFair?.serverSeed,
    serverSeedHash: ourGame.provablyFair?.serverSeedHash,
    peakMultiplier: ourGame.peakMultiplier,
    isValidRug: Math.abs(ourGame.prices[ourGame.prices.length - 1] - 0.020000000000000018) < 0.001
  };
}
```

### Safe PRNG Data Extraction for COOLDOWN

```javascript
function extractPRNGDataSafe(data, trackedGameId) {
  // CRITICAL: Only extract for tracked games
  if (!trackedGameId) {
    console.error('❌ Cannot extract PRNG data without tracked game ID');
    return null;
  }

  if (!data.gameHistory || !Array.isArray(data.gameHistory)) {
    console.error('❌ No game history available for PRNG extraction');
    return null;
  }

  // Find OUR game specifically
  const ourGame = data.gameHistory.find(g => g.id === trackedGameId);

  if (!ourGame) {
    console.error(`❌ Tracked game ${trackedGameId} not in history for PRNG extraction`);
    return null;
  }

  // Verify PRNG data is complete
  if (!ourGame.provablyFair?.serverSeed) {
    console.error(`❌ No server seed revealed for game ${trackedGameId}`);
    return null;
  }

  return {
    // Verified ended game PRNG
    endedGame: {
      id: ourGame.id,
      serverSeed: ourGame.provablyFair.serverSeed,  // NOW REVEALED!
      serverSeedHash: ourGame.provablyFair.serverSeedHash,
      gameVersion: ourGame.gameVersion,
      timestamp: parseGameIdToTimestamp(ourGame.id)
    },

    // Next game PRNG (seed not yet revealed)
    nextGame: {
      id: data.gameId,
      serverSeedHash: data.provablyFair?.serverSeedHash,  // Hash only
      version: data.provablyFair?.version
    },

    // Verification data
    verification: {
      gameIdMatch: ourGame.id === trackedGameId,
      serverSeedRevealed: !!ourGame.provablyFair?.serverSeed,
      hasServerSeedHash: !!ourGame.provablyFair?.serverSeedHash
    }
  };
}
```

## Updated Key Safety Rules for COOLDOWN Phase

1. **NEVER extract game history without a tracked game ID** - If we didn't track the game from start, skip it completely
2. **ALWAYS find games by ID, never by array index** - Never assume `gameHistory[0]` is the game we want
3. **VERIFY data integrity before processing** - Check for rug price, server seed, complete price history
4. **LOG all safety check failures** - Help debug when data extraction fails
5. **SKIP and wait for complete games** - Better to miss data than have corrupted data
6. **Track game state transitions** - From start → rug → cooldown with verified IDs
7. **Implement data verification checksums** - Verify price arrays, game completion, etc.

## Implementation Notes

- The cooldown phase provides the **most complete data** about the just-ended game
- Server seeds are revealed during this phase, enabling PRNG analysis
- The next game ID is pre-generated, allowing for predictive analysis
- Timer typically starts at 15000ms (15 seconds) and counts down to 0
- All game state variables are reset in preparation for the next game