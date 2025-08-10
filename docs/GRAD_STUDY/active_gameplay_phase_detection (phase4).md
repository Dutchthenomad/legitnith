# PHASE 4: ACTIVE GAMEPLAY BEGINS - The Critical Transition

## The Exact Moment: cooldownTimer Reaches 0

When `cooldownTimer` counts down from 100ms to 0, the game **instantly begins**:

### Last Moment of Pre-Round (100ms remaining)

```json
{
  "gameId": "20250807-f4b38de436964086",
  "cooldownTimer": 100,  // Final 100ms
  "cooldownPaused": false,
  "allowPreRoundBuys": true,  // Still allowing pre-round buys
  "active": false,  // NOT YET ACTIVE (implied)
  "leaderboard": [  // Shows pre-round buyers
    {
      "username": "Godisgood",
      "positionQty": 0.25,
      "avgCost": 1,
      "pnl": 0,  // No profit yet
      "hasActiveTrades": true  // Has pre-round position
    }
    // ... more pre-round buyers
  ]
}
```

### THE GAME BEGINS (cooldownTimer hits 0)

```json
{
  "active": true,  // FLIPS TO TRUE!
  "price": 1,  // Starts at 1
  "prices": [1],  // Price array begins
  "trades": [],  // Empty trades array
  "rugged": false,
  "tickCount": 0,  // Starts at 0
  "cooldownTimer": 0,  // Timer expired
  "allowPreRoundBuys": false,  // PRE-ROUND ENDS!
  "leaderboard": [],  // RESETS TO EMPTY!
  "gameId": "20250807-f4b38de436964086"  // Same game ID
}
```

### First Tick After Game Start

```json
{
  "active": true,
  "price": 1.0189900463112405,  // First price movement!
  "prices": [1, 1.0189900463112405],
  "tradeCount": 14,  // Trades begin immediately
  "tickCount": 1,  // First tick
  "leaderboard": []  // Still empty initially
}
```

### Second Tick - Leaderboard Populates

```json
{
  "tickCount": 2,
  "price": 1.0428799758875935,
  "leaderboard": [  // NOW SHOWS ACTIVE POSITIONS!
    {
      "username": "Godisgood",
      "pnl": 0.00474751,  // Now showing profit!
      "pnlPercent": 1.899004,
      "positionQty": 0.25,
      "avgCost": 1
    }
  ]
}
```

## Safe Game Start Detection Logic

```javascript
class SafeGameStartDetector {
  constructor() {
    this.lastCooldownTimer = null;
    this.gameStarted = false;
    this.preRoundPositions = new Map();
    this.trackedGameId = null;
  }

  setTrackedGame(gameId) {
    this.trackedGameId = gameId;
    this.gameStarted = false;
    this.preRoundPositions.clear();
    console.log(`🎯 Now tracking game start for: ${gameId}`);
  }

  detectGameStart(data) {
    // SAFETY: Only detect for our tracked game
    if (this.trackedGameId && data.gameId !== this.trackedGameId) {
      return null;
    }

    // METHOD 1: Cooldown Timer Reaches 0
    if (this.lastCooldownTimer > 0 &&
        data.cooldownTimer === 0 &&
        data.active === true) {
      return {
        method: 'TIMER_EXPIRY',
        timestamp: Date.now(),
        gameId: data.gameId,
        verified: data.gameId === this.trackedGameId
      };
    }

    // METHOD 2: Active Flips with Price at 1
    if (!this.gameStarted &&
        data.active === true &&
        data.price === 1 &&
        data.tickCount === 0) {
      return {
        method: 'ACTIVE_FLIP',
        timestamp: Date.now(),
        gameId: data.gameId,
        verified: data.gameId === this.trackedGameId
      };
    }

    // METHOD 3: First Price Movement
    if (data.active &&
        data.tickCount === 1 &&
        data.prices &&
        data.prices.length === 2 &&
        data.prices[0] === 1) {
      return {
        method: 'FIRST_TICK',
        timestamp: Date.now(),
        gameId: data.gameId,
        firstPrice: data.prices[1],
        verified: data.gameId === this.trackedGameId
      };
    }

    return null;
  }

  onGameStateUpdate(data) {
    // SAFETY: Only process our tracked game
    if (this.trackedGameId && data.gameId !== this.trackedGameId) {
      return;
    }

    const gameStart = this.detectGameStart(data);

    if (gameStart && !this.gameStarted) {
      console.log('🚀 GAME STARTED!', gameStart);
      this.handleGameStart(data);
    }

    this.lastCooldownTimer = data.cooldownTimer;
  }

  handleGameStart(data) {
    // SAFETY: Verify this is our tracked game
    if (data.gameId !== this.trackedGameId) {
      console.error(`❌ Game start for wrong game! Expected: ${this.trackedGameId}, Got: ${data.gameId}`);
      return;
    }

    this.gameStarted = true;

    // Log critical start conditions
    const startState = {
      gameId: data.gameId,
      startTime: Date.now(),
      initialPrice: data.price,
      preRoundBuyers: this.preRoundPositions.size,
      connectedPlayers: data.connectedPlayers,

      // Verify expected conditions
      checks: {
        isActive: data.active === true,
        timerIsZero: data.cooldownTimer === 0,
        priceIsOne: data.price === 1,
        tickIsZero: data.tickCount === 0,
        preRoundEnded: data.allowPreRoundBuys === false,
        leaderboardEmpty: Array.isArray(data.leaderboard) && data.leaderboard.length === 0,
        gameIdMatch: data.gameId === this.trackedGameId
      },

      // Data integrity verification
      integrity: {
        allChecksPass: this.validateGameStart(data),
        hasValidPriceArray: Array.isArray(data.prices) && data.prices.length >= 1,
        tradesArrayReset: Array.isArray(data.trades)
      }
    };

    // Only log if all integrity checks pass
    if (startState.integrity.allChecksPass) {
      this.logGameStart(startState);
    } else {
      console.error('❌ Game start integrity check FAILED:', startState.checks);
    }
  }

  validateGameStart(data) {
    return (
      data.active === true &&
      data.cooldownTimer === 0 &&
      data.price === 1 &&
      data.tickCount === 0 &&
      data.allowPreRoundBuys === false &&
      data.gameId === this.trackedGameId
    );
  }

  logGameStart(startState) {
    console.log('✅ VERIFIED GAME START:', {
      gameId: startState.gameId,
      method: 'TIMER_EXPIRY',
      initialPrice: startState.initialPrice,
      preRoundParticipants: startState.preRoundBuyers
    });

    // Save game start data
    this.saveGameStart(startState);
  }

  reset() {
    console.log(`🔄 Resetting game start detector (was tracking: ${this.trackedGameId})`);
    this.lastCooldownTimer = null;
    this.gameStarted = false;
    this.preRoundPositions.clear();
    this.trackedGameId = null;
  }

  saveGameStart(startState) {
    // Your storage implementation
  }
}
```

## Transition Indicators Table

### From Pre-Round to Active Game

| Field             | Pre-Round (100ms left) | Game Start (0ms) | First Tick       |
|-------------------|------------------------|------------------|------------------|
| `cooldownTimer`     | 100                    | 0                | 0                |
| `active`            | `false`                  | `true`             | `true`             |
| `allowPreRoundBuys` | `true`                   | `false`            | `false`            |
| `price`             | 1                      | 1                | 1.018...         |
| `tickCount`         | 0                      | 0                | 1                |
| `leaderboard`       | Pre-round positions    | `[]` (empty)       | Active positions |
| `trades`            | Pre-round trades       | `[]` (reset)       | New trades       |

## Complete Game Cycle Monitor with Safety

```javascript
class SafeCompleteGameCycleMonitor {
  constructor() {
    this.phases = {
      WAITING: 'WAITING',
      COOLDOWN: 'COOLDOWN',
      PRE_ROUND: 'PRE_ROUND',
      ACTIVE: 'ACTIVE',
      RUG: 'RUG'
    };

    this.currentPhase = this.phases.WAITING;
    this.phaseHistory = [];
    this.trackedGameId = null;
  }

  setTrackedGame(gameId) {
    this.trackedGameId = gameId;
    this.currentPhase = this.phases.WAITING;
    this.phaseHistory = [];
    console.log(`🎮 Started tracking complete cycle for: ${gameId}`);
  }

  updatePhase(data) {
    // SAFETY: Only process our tracked game
    if (this.trackedGameId && data.gameId !== this.trackedGameId) {
      return this.currentPhase;
    }

    const newPhase = this.detectPhase(data);

    if (newPhase !== this.currentPhase) {
      this.logPhaseTransition(this.currentPhase, newPhase, data);
      this.currentPhase = newPhase;
    }

    return this.currentPhase;
  }

  detectPhase(data) {
    // RUG EVENT
    if (data.rugged === true) {
      return this.phases.RUG;
    }

    // ACTIVE GAME
    if (data.active === true && !data.rugged) {
      return this.phases.ACTIVE;
    }

    // COOLDOWN (15s to 10s)
    if (!data.active && data.cooldownTimer > 10000) {
      return this.phases.COOLDOWN;
    }

    // PRE-ROUND (10s to 0)
    if (!data.active &&
        data.cooldownTimer <= 10000 &&
        data.cooldownTimer > 0 &&
        data.allowPreRoundBuys === true) {
      return this.phases.PRE_ROUND;
    }

    // GAME STARTING (transition moment)
    if (data.cooldownTimer === 0 && data.active === true) {
      return this.phases.ACTIVE;
    }

    return this.phases.WAITING;
  }

  logPhaseTransition(from, to, data) {
    const transition = {
      timestamp: Date.now(),
      from,
      to,
      gameId: data.gameId,
      verified: data.gameId === this.trackedGameId,

      // Special handling for game start
      gameStart: (from === this.phases.PRE_ROUND && to === this.phases.ACTIVE),

      metadata: {
        cooldownTimer: data.cooldownTimer,
        active: data.active,
        price: data.price,
        tickCount: data.tickCount,
        allowPreRoundBuys: data.allowPreRoundBuys
      }
    };

    // Only log verified transitions
    if (transition.verified) {
      this.phaseHistory.push(transition);

      if (transition.gameStart) {
        console.log('🎮 VERIFIED GAME STARTED!', transition);
      } else {
        console.log(`📍 PHASE TRANSITION: ${from} → ${to}`, transition);
      }
    }
  }

  getVerifiedPhaseHistory() {
    return this.phaseHistory.filter(t => t.verified);
  }

  getCurrentPhase() {
    return this.currentPhase;
  }

  reset() {
    console.log(`🔄 Resetting cycle monitor (was tracking: ${this.trackedGameId})`);
    this.currentPhase = this.phases.WAITING;
    this.phaseHistory = [];
    this.trackedGameId = null;
  }
}
```

## Active Gameplay State Monitor

```javascript
class ActiveGameplayMonitor {
  constructor() {
    this.trackedGameId = null;
    this.gameStartTime = null;
    this.tickHistory = [];
    this.priceHistory = [];
  }

  setTrackedGame(gameId) {
    this.trackedGameId = gameId;
    this.gameStartTime = null;
    this.tickHistory = [];
    this.priceHistory = [];
    console.log(`📊 Monitoring active gameplay for: ${gameId}`);
  }

  onActiveGameUpdate(data) {
    // SAFETY: Only monitor our tracked game
    if (data.gameId !== this.trackedGameId || !data.active) {
      return;
    }

    // First tick detection
    if (data.tickCount === 1 && !this.gameStartTime) {
      this.gameStartTime = Date.now();
      console.log('⏱️ First tick detected, gameplay officially started');
    }

    // Track price movements
    if (data.prices && data.prices.length > this.priceHistory.length) {
      this.priceHistory = [...data.prices];
      
      const tickData = {
        tick: data.tickCount,
        price: data.price,
        timestamp: Date.now(),
        leaderboardSize: data.leaderboard?.length || 0,
        tradeCount: data.trades?.length || 0
      };

      this.tickHistory.push(tickData);
    }

    // Monitor for rug potential (high prices)
    if (data.price > 10) {
      console.log(`⚠️ High price detected: ${data.price}x - RUG RISK INCREASING`);
    }
  }

  getGameplayStats() {
    if (!this.gameStartTime) return null;

    const currentTime = Date.now();
    const gameDuration = currentTime - this.gameStartTime;
    const currentPrice = this.priceHistory[this.priceHistory.length - 1] || 1;
    const peakPrice = Math.max(...this.priceHistory);

    return {
      gameId: this.trackedGameId,
      duration: gameDuration,
      currentTick: this.tickHistory.length,
      currentPrice,
      peakPrice,
      totalPriceUpdates: this.priceHistory.length,
      averageTickDuration: gameDuration / this.tickHistory.length
    };
  }

  reset() {
    console.log(`🔄 Resetting gameplay monitor (was tracking: ${this.trackedGameId})`);
    this.trackedGameId = null;
    this.gameStartTime = null;
    this.tickHistory = [];
    this.priceHistory = [];
  }
}
```

## Complete Game Cycle Summary

### The Full Perpetual Cycle

1. **RUG EVENT** → `rugged: true`, price crashes to 0.02
2. **COOLDOWN BEGINS** → `active: false`, timer at 15000ms  
3. **PRE-ROUND STARTS** → Timer hits 10000ms, `allowPreRoundBuys: true`
4. **GAME BEGINS** → Timer hits 0, `active: true`, trading resumes
5. **ACTIVE GAMEPLAY** → Price movements, trades occur
6. **Return to Step 1** → Perpetual cycle continues

### Critical Game Start Moments

- **100ms remaining**: Last chance for pre-round buys
- **0ms**: Timer expires, game becomes active
- **Tick 1**: First price movement, trading begins
- **Tick 2+**: Leaderboard populates with active positions

## Safety Rules for Active Gameplay Phase

1. **VERIFY game ID matches** - Never process data for wrong games
2. **VALIDATE timer transition** - Ensure proper 100ms → 0ms → active sequence  
3. **CHECK state consistency** - Verify `active: true` with `cooldownTimer: 0`
4. **MONITOR price initialization** - Game should start at exactly `price: 1`
5. **TRACK tick progression** - Ensure `tickCount` starts at 0 and increments properly
6. **VALIDATE array resets** - Leaderboard and trades arrays should reset appropriately
7. **LOG transition integrity** - Help debug any state inconsistencies

## Implementation Notes

### The Critical 100ms → 0ms Transition

The transition from `cooldownTimer: 100ms` to `0ms` is the **precise moment** when:

- **Pre-round buying ends** (`allowPreRoundBuys` → `false`)
- **Game becomes active** (`active` → `true`)  
- **Full trading resumes** (both buy and sell allowed)
- **Price begins to move** from 1.0
- **The chaos begins anew!**

### Key Detection Strategies

1. **Timer Expiry Method** - Most reliable, watches for 100ms → 0ms transition
2. **Active Flip Method** - Detects `active: false` → `active: true` with proper conditions
3. **First Tick Method** - Confirms game start with first price movement

### Bot Implementation Considerations

- **Pre-position during pre-round** for optimal entry
- **Monitor timer countdown** for precise entry timing  
- **Validate all state transitions** to ensure data integrity
- **Track price movements** immediately after game start
- **Prepare for rapid price action** once gameplay begins

This marks the beginning of the **most volatile and profitable phase** of the game cycle! 🚀