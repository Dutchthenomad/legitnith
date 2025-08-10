# Phase 3: GAME ACTIVATION - Pre-Round Buy Window - Final Implementation

## The Critical 10-Second Mark

When `cooldownTimer` reaches exactly **10000ms (10 seconds)**, a crucial change occurs:

```json
{
  "cooldownTimer": 10000,
  "allowPreRoundBuys": true,  // FLIPS TO TRUE!
  "active": false,             // Still not active
  "leaderboard": []            // Resets to empty
}
```

## Complete State at 10-Second Mark

```json
{
  "gameId": "20250807-f4b38de436964086",
  "cooldownTimer": 10000,
  "cooldownPaused": false,
  "allowPreRoundBuys": true,    // KEY CHANGE!
  "leaderboard": [],             // CLEARED!
  "connectedPlayers": 196,
  "active": false,               // Still false
  "price": 1,                    // Still at 1
  "rugged": false,
  "tickCount": 0,
  "provablyFair": {
    "serverSeedHash": "a64cf432b36edfba9bd856d8053b6396613fbf66f413ed8da9330bdefb589a6c",
    "version": "v3"
  }
}
```

## What Players Can Do During Pre-Round (10 seconds to 0)

1. **BUY positions** - Enter the game early
2. **Place SIDE BETS** - Set up conditional bets  
3. **CANNOT SELL** - No selling until game is active

## Production-Ready Pre-Round Detection and Processing

### Complete Pre-Round Handler with Database Integration

```javascript
class ProductionPreRoundHandler {
  constructor(dbConnection, cacheConnection, tradingAnalyzer) {
    this.db = dbConnection;
    this.cache = cacheConnection;
    this.tradingAnalyzer = tradingAnalyzer;
    
    this.inPreRound = false;
    this.preRoundStartTime = null;
    this.preRoundBuys = [];
    this.preRoundSidebets = [];
    this.trackedGameId = null;
  }

  setTrackedGame(gameId) {
    this.trackedGameId = gameId;
    console.log(`🎯 Pre-round handler tracking: ${gameId}`);
  }

  async onGameStateUpdate(data) {
    // SAFETY CHECK: Only process updates for our tracked game
    if (this.trackedGameId && data.gameId !== this.trackedGameId) {
      console.warn(`⚠️ Game ID mismatch in pre-round. Expected: ${this.trackedGameId}, Got: ${data.gameId}`);
      return;
    }

    // DETECT PRE-ROUND START (10 second mark)
    if (!this.inPreRound &&
        data.cooldownTimer === 10000 &&
        data.allowPreRoundBuys === true &&
        !data.active) {

      await this.onPreRoundStart(data);
    }

    // TRACK PRE-ROUND PROGRESS
    if (this.inPreRound && data.cooldownTimer > 0 && data.cooldownTimer <= 10000) {
      await this.trackPreRound(data);
    }

    // DETECT PRE-ROUND END (game starts)
    if (this.inPreRound && data.active === true) {
      await this.onPreRoundEnd(data);
    }
  }

  async onPreRoundStart(data) {
    // SAFETY: Verify this is our tracked game
    if (this.trackedGameId && data.gameId !== this.trackedGameId) {
      console.error(`❌ Pre-round start for wrong game! Expected: ${this.trackedGameId}, Got: ${data.gameId}`);
      return;
    }

    this.inPreRound = true;
    this.preRoundStartTime = Date.now();
    this.preRoundBuys = [];
    this.preRoundSidebets = [];

    console.log('🟢 PRE-ROUND BUYING ENABLED', {
      gameId: data.gameId,
      timeRemaining: data.cooldownTimer,
      timestamp: this.preRoundStartTime,
      verified: data.gameId === this.trackedGameId
    });

    const preRoundStartData = {
      phase: 'PRE_ROUND_START',
      gameId: data.gameId,
      cooldownTimer: data.cooldownTimer,
      allowPreRoundBuys: data.allowPreRoundBuys,
      leaderboard: data.leaderboard,
      connectedPlayers: data.connectedPlayers,
      startTime: this.preRoundStartTime,
      
      dataIntegrity: {
        gameIdMatch: data.gameId === this.trackedGameId,
        leaderboardCleared: Array.isArray(data.leaderboard) && data.leaderboard.length === 0,
        correctTimer: data.cooldownTimer === 10000,
        buyingEnabled: data.allowPreRoundBuys === true
      }
    };

    // Store pre-round start event
    await this.logPreRoundStart(preRoundStartData);
    
    // Update game phase in database
    await this.updateGamePhase(data.gameId, 'PRE_ROUND');
    
    // Cache pre-round state
    this.cachePreRoundState(preRoundStartData);
  }

  async trackPreRound(data) {
    // SAFETY: Only track for our game
    if (data.gameId !== this.trackedGameId) {
      return;
    }

    // Monitor for pre-round trades
    if (data.trades && data.trades.length > 0) {
      const newTrades = data.trades.filter(t =>
        !this.preRoundBuys.find(existing => existing.id === t.id)
      );

      for (const trade of newTrades) {
        if (trade.type === 'buy') {
          await this.processPreRoundBuy(trade, data);
        } else {
          console.warn('⚠️ Non-buy trade attempted in pre-round:', trade);
        }
      }
    }

    // Monitor for side bets
    if (data.leaderboard) {
      await this.trackPreRoundSidebets(data.leaderboard, data);
    }

    // Update countdown cache
    this.updateCountdownCache(data);
  }

  async processPreRoundBuy(trade, gameData) {
    const preRoundTrade = {
      id: trade.id,
      gameId: trade.gameId,
      playerId: trade.playerId,
      amount: trade.amount,
      qty: trade.qty,
      coin: trade.coin,
      tickIndex: trade.tickIndex,
      timeRemaining: gameData.cooldownTimer,
      phase: 'PRE_ROUND',
      timestamp: Date.now()
    };

    this.preRoundBuys.push(preRoundTrade);

    try {
      // Store pre-round buy in database
      await this.db.query(`
        INSERT INTO trades (
          id, game_id, player_id, type, tick_index, amount, quantity, 
          coin, timestamp_ms, phase, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
        ON CONFLICT (id) DO UPDATE SET
          phase = EXCLUDED.phase,
          timestamp_ms = EXCLUDED.timestamp_ms
      `, [
        trade.id, trade.gameId, trade.playerId, 'buy', trade.tickIndex,
        trade.amount, trade.qty, trade.coin, Date.now(), 'PRE_ROUND'
      ]);

      // Store pre-round specific data
      await this.db.query(`
        INSERT INTO pre_round_activities (
          game_id, trade_id, player_id, activity_type, amount, 
          time_remaining_ms, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
        ON CONFLICT (trade_id) DO NOTHING
      `, [
        trade.gameId, trade.id, trade.playerId, 'BUY', 
        trade.amount, gameData.cooldownTimer
      ]);

      console.log('📈 Pre-round buy recorded:', {
        tradeId: trade.id,
        player: trade.playerId.substring(0, 20) + '...',
        amount: trade.amount + ' SOL',
        timeRemaining: gameData.cooldownTimer + 'ms'
      });

    } catch (error) {
      console.error(`❌ Error storing pre-round buy: ${error}`);
    }
  }

  async trackPreRoundSidebets(leaderboard, gameData) {
    for (const player of leaderboard) {
      if (player.sidebetActive && player.sideBet) {
        const sidebet = {
          playerId: player.id,
          username: player.username,
          gameId: gameData.gameId,
          startTick: player.sideBet.startedAtTick,
          endTick: player.sideBet.end,
          betAmount: player.sideBet.betAmount,
          targetMultiplier: player.sideBet.xPayout,
          coinAddress: player.sideBet.coinAddress,
          timeRemaining: gameData.cooldownTimer,
          timestamp: Date.now()
        };

        // Check if this is a new sidebet
        if (!this.preRoundSidebets.find(sb => sb.playerId === player.id)) {
          this.preRoundSidebets.push(sidebet);
          await this.storePreRoundSidebet(sidebet);
        }
      }
    }
  }

  async storePreRoundSidebet(sidebet) {
    try {
      await this.db.query(`
        INSERT INTO side_bets (
          game_id, player_id, start_tick, end_tick, bet_amount,
          target_multiplier, coin_address, active, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, true, NOW())
        ON CONFLICT (game_id, player_id) DO UPDATE SET
          bet_amount = EXCLUDED.bet_amount,
          target_multiplier = EXCLUDED.target_multiplier,
          updated_at = NOW()
      `, [
        sidebet.gameId, sidebet.playerId, sidebet.startTick,
        sidebet.endTick, sidebet.betAmount, sidebet.targetMultiplier,
        sidebet.coinAddress
      ]);

      // Store pre-round activity
      await this.db.query(`
        INSERT INTO pre_round_activities (
          game_id, player_id, activity_type, amount, 
          time_remaining_ms, metadata, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
      `, [
        sidebet.gameId, sidebet.playerId, 'SIDEBET',
        sidebet.betAmount, sidebet.timeRemaining,
        JSON.stringify({
          targetMultiplier: sidebet.targetMultiplier,
          endTick: sidebet.endTick
        })
      ]);

      console.log('🎰 Pre-round sidebet recorded:', {
        player: sidebet.username,
        bet: sidebet.betAmount + ' SOL',
        target: sidebet.targetMultiplier + 'x'
      });

    } catch (error) {
      console.error(`❌ Error storing pre-round sidebet: ${error}`);
    }
  }

  async onPreRoundEnd(data) {
    // SAFETY: Verify game transition
    if (data.gameId !== this.trackedGameId) {
      console.error(`❌ Pre-round end for wrong game! Expected: ${this.trackedGameId}, Got: ${data.gameId}`);
      return;
    }

    const preRoundDuration = Date.now() - this.preRoundStartTime;

    console.log('🏁 PRE-ROUND ENDED', {
      gameId: data.gameId,
      duration: preRoundDuration,
      totalPreRoundBuys: this.preRoundBuys.length,
      totalSidebets: this.preRoundSidebets.length,
      gameNowActive: data.active,
      verified: data.gameId === this.trackedGameId
    });

    // Process and store pre-round summary
    const summary = await this.generatePreRoundSummary(data, preRoundDuration);
    await this.storePreRoundSummary(summary);

    // Update game phase
    await this.updateGamePhase(data.gameId, 'ACTIVE');

    // Cache final pre-round state
    this.cachePreRoundCompletion(summary);

    // Reset for next game
    this.reset();
  }

  async generatePreRoundSummary(data, duration) {
    const summary = {
      gameId: data.gameId,
      duration: duration,
      startTime: this.preRoundStartTime,
      endTime: Date.now(),
      
      // Trading activity
      totalBuys: this.preRoundBuys.length,
      totalBuyVolume: this.preRoundBuys.reduce((sum, buy) => sum + buy.amount, 0),
      averageBuySize: this.preRoundBuys.length > 0 
        ? this.preRoundBuys.reduce((sum, buy) => sum + buy.amount, 0) / this.preRoundBuys.length
        : 0,
      uniqueBuyers: new Set(this.preRoundBuys.map(buy => buy.playerId)).size,
      
      // Sidebet activity
      totalSidebets: this.preRoundSidebets.length,
      totalSidebetVolume: this.preRoundSidebets.reduce((sum, sb) => sum + sb.betAmount, 0),
      averageSidebetSize: this.preRoundSidebets.length > 0
        ? this.preRoundSidebets.reduce((sum, sb) => sum + sb.betAmount, 0) / this.preRoundSidebets.length
        : 0,
      
      // System metrics
      connectedPlayers: data.connectedPlayers,
      participationRate: data.connectedPlayers > 0 
        ? (new Set([...this.preRoundBuys.map(b => b.playerId), ...this.preRoundSidebets.map(s => s.playerId)]).size / data.connectedPlayers) * 100
        : 0
    };

    return summary;
  }

  async storePreRoundSummary(summary) {
    try {
      await this.db.query(`
        INSERT INTO pre_round_summaries (
          game_id, duration_ms, total_buys, total_buy_volume, 
          total_sidebets, total_sidebet_volume, unique_participants,
          connected_players, participation_rate, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
        ON CONFLICT (game_id) DO UPDATE SET
          duration_ms = EXCLUDED.duration_ms,
          total_buys = EXCLUDED.total_buys,
          total_buy_volume = EXCLUDED.total_buy_volume,
          updated_at = NOW()
      `, [
        summary.gameId, summary.duration, summary.totalBuys,
        summary.totalBuyVolume, summary.totalSidebets, 
        summary.totalSidebetVolume, summary.uniqueBuyers,
        summary.connectedPlayers, summary.participationRate
      ]);

      console.log(`📊 Pre-round summary stored for: ${summary.gameId}`);

    } catch (error) {
      console.error(`❌ Error storing pre-round summary: ${error}`);
    }
  }

  async updateGamePhase(gameId, phase) {
    try {
      await this.db.query(`
        UPDATE games SET 
          phase = $2,
          updated_at = NOW()
        WHERE id = $1
      `, [gameId, phase]);

    } catch (error) {
      console.error(`❌ Error updating game phase: ${error}`);
    }
  }

  async logPreRoundStart(data) {
    try {
      await this.db.query(`
        INSERT INTO game_events (
          game_id, event_type, event_data, timestamp_ms, created_at
        ) VALUES ($1, $2, $3, $4, NOW())
      `, [
        data.gameId, 'PRE_ROUND_START', 
        JSON.stringify(data), data.startTime
      ]);

    } catch (error) {
      console.error(`❌ Error logging pre-round start: ${error}`);
    }
  }

  cachePreRoundState(data) {
    const cacheKey = `preround:${data.gameId}`;
    const state = {
      gameId: data.gameId,
      phase: 'PRE_ROUND',
      startTime: data.startTime,
      cooldownTimer: data.cooldownTimer,
      allowPreRoundBuys: data.allowPreRoundBuys,
      connectedPlayers: data.connectedPlayers,
      lastUpdate: Date.now()
    };

    this.cache.setex(cacheKey, 600, JSON.stringify(state)); // 10 minute expiry
  }

  updateCountdownCache(data) {
    const cacheKey = `countdown:${data.gameId}`;
    const countdown = {
      gameId: data.gameId,
      cooldownTimer: data.cooldownTimer,
      phase: 'PRE_ROUND',
      lastUpdate: Date.now()
    };

    this.cache.setex(cacheKey, 15, JSON.stringify(countdown)); // 15 second expiry
  }

  cachePreRoundCompletion(summary) {
    const cacheKey = `preround_complete:${summary.gameId}`;
    this.cache.setex(cacheKey, 3600, JSON.stringify(summary)); // 1 hour expiry
  }

  reset() {
    console.log(`🔄 Resetting pre-round handler (was tracking: ${this.trackedGameId})`);
    this.inPreRound = false;
    this.preRoundStartTime = null;
    this.preRoundBuys = [];
    this.preRoundSidebets = [];
    this.trackedGameId = null;
  }
}
```

### Pre-Round Activity Validator

```javascript
class PreRoundActivityValidator {
  constructor(dbConnection) {
    this.db = dbConnection;
    this.trackedGameId = null;
    this.currentCooldownTimer = null;
  }

  setTrackedGame(gameId) {
    this.trackedGameId = gameId;
  }

  updateTimer(timer) {
    this.currentCooldownTimer = timer;
  }

  async validatePreRoundTrade(trade, gameData) {
    // SAFETY: Verify this is our tracked game
    if (gameData.gameId !== this.trackedGameId) {
      console.error(`❌ Trade validation for wrong game! Expected: ${this.trackedGameId}, Got: ${gameData.gameId}`);
      return { valid: false, reason: 'Game ID mismatch' };
    }

    // VALIDATION: Only buys allowed in pre-round
    if (trade.type !== 'buy') {
      console.error('❌ INVALID: Only buys allowed in pre-round!');
      return { valid: false, reason: 'Only buy trades allowed in pre-round' };
    }

    // VALIDATION: Must be in pre-round phase
    if (!gameData.allowPreRoundBuys || gameData.active) {
      console.error('❌ INVALID: Not in pre-round phase!');
      return { valid: false, reason: 'Not in pre-round phase' };
    }

    // VALIDATION: Timer bounds
    if (gameData.cooldownTimer > 10000 || gameData.cooldownTimer <= 0) {
      console.error(`❌ INVALID: Timer out of pre-round bounds: ${gameData.cooldownTimer}`);
      return { valid: false, reason: 'Timer out of pre-round bounds' };
    }

    // VALIDATION: Trade amounts
    if (trade.amount <= 0 || trade.qty <= 0) {
      console.error('❌ INVALID: Invalid trade amounts');
      return { valid: false, reason: 'Invalid trade amounts' };
    }

    return {
      valid: true,
      tradeData: {
        phase: 'PRE_ROUND',
        tradeId: trade.id,
        playerId: trade.playerId,
        amount: trade.amount,
        timestamp: Date.now(),
        timeUntilStart: this.currentCooldownTimer,
        gameId: gameData.gameId,
        verified: true
      }
    };
  }

  async validatePreRoundState(data) {
    const errors = [];

    // SAFETY: Only validate our tracked game
    if (data.gameId !== this.trackedGameId) {
      return ['Game ID mismatch - not validating'];
    }

    if (data.allowPreRoundBuys && data.active) {
      errors.push('Cannot have pre-round buys while game is active');
    }

    if (data.allowPreRoundBuys && data.cooldownTimer > 10000) {
      errors.push('Pre-round buys should only be allowed at ≤10 seconds');
    }

    if (data.allowPreRoundBuys && data.cooldownTimer === 0) {
      errors.push('Pre-round buys should be disabled when timer reaches 0');
    }

    if (data.allowPreRoundBuys && !Array.isArray(data.leaderboard)) {
      errors.push('Leaderboard should be an array during pre-round');
    }

    if (data.allowPreRoundBuys && data.leaderboard.length > 0) {
      errors.push('Leaderboard should be empty during pre-round');
    }

    return errors;
  }
}
```

## Phase Comparison Table

| Field             | Cooldown (15s-10s) | Pre-Round (10s-0s) |
|-------------------|--------------------|--------------------|
| `active`            | `false`              | `false`              |
| `cooldownTimer`     | 15000 → 10001      | 10000 → 1          |
| `allowPreRoundBuys` | `false`              | `true`               |
| `leaderboard`       | Previous game's    | Empty `[]`           |
| Player Actions    | None               | Can BUY only       |
| Side Bets         | Cannot place       | Can place          |

## Database Schema for Pre-Round Tracking

### Pre-Round Activities Table
```sql
CREATE TABLE pre_round_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id VARCHAR(64) NOT NULL REFERENCES games(id),
    trade_id VARCHAR(64), -- NULL for non-trade activities
    player_id VARCHAR(128) NOT NULL,
    activity_type VARCHAR(20) NOT NULL, -- 'BUY', 'SIDEBET'
    amount DECIMAL(20,12) NOT NULL,
    time_remaining_ms INTEGER NOT NULL, -- Countdown timer when activity occurred
    metadata JSONB, -- Additional data (sidebet details, etc.)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT valid_activity_type CHECK (activity_type IN ('BUY', 'SIDEBET')),
    CONSTRAINT valid_time_remaining CHECK (time_remaining_ms > 0 AND time_remaining_ms <= 10000)
);

CREATE INDEX idx_pre_round_activities_game_id ON pre_round_activities(game_id);
CREATE INDEX idx_pre_round_activities_player_id ON pre_round_activities(player_id);
CREATE INDEX idx_pre_round_activities_type ON pre_round_activities(activity_type);
```

### Pre-Round Summaries Table
```sql
CREATE TABLE pre_round_summaries (
    game_id VARCHAR(64) PRIMARY KEY REFERENCES games(id),
    duration_ms INTEGER NOT NULL,
    total_buys INTEGER DEFAULT 0,
    total_buy_volume DECIMAL(20,12) DEFAULT 0,
    total_sidebets INTEGER DEFAULT 0,
    total_sidebet_volume DECIMAL(20,12) DEFAULT 0,
    unique_participants INTEGER DEFAULT 0,
    connected_players INTEGER DEFAULT 0,
    participation_rate DECIMAL(5,2) DEFAULT 0, -- Percentage
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

## Analytics Queries

### Pre-Round Performance Analysis
```sql
-- Pre-round participation trends
SELECT 
    DATE_TRUNC('hour', created_at) as hour_bucket,
    AVG(participation_rate) as avg_participation,
    AVG(total_buy_volume) as avg_buy_volume,
    AVG(duration_ms) as avg_duration_ms
FROM pre_round_summaries
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY hour_bucket
ORDER BY hour_bucket DESC;

-- Top pre-round players
SELECT 
    player_id,
    COUNT(*) as pre_round_activities,
    SUM(amount) as total_volume,
    AVG(amount) as avg_activity_size,
    COUNT(DISTINCT game_id) as games_participated
FROM pre_round_activities
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY player_id
ORDER BY total_volume DESC
LIMIT 50;
```

## Key Detection Points for Pre-Round

1. **Timer exactly 10000ms** - The trigger for pre-round activation
2. **`allowPreRoundBuys: true`** - Enables player buying
3. **`leaderboard: []`** - Cleared from previous game
4. **Still `active: false`** - Game not yet started
5. **Player trades change** - Only 'buy' types allowed

## Safety Rules for Pre-Round Phase

1. **ALWAYS verify game ID matches** - Never process data for wrong games
2. **VALIDATE state transitions** - Ensure proper cooldown → pre-round → active flow
3. **CHECK allowPreRoundBuys flag** - Must be true for valid pre-round
4. **VERIFY timer bounds** - Pre-round only valid between 10000ms and 1ms
5. **MONITOR trade types** - Only 'buy' trades should appear
6. **VALIDATE leaderboard state** - Should be empty array during pre-round
7. **LOG state validation errors** - Help debug phase transition issues
8. **STORE all activities** - Complete database persistence for analysis
9. **TRACK participation metrics** - Monitor player engagement trends

## Implementation Notes

- The **10-second pre-round window** is crucial for strategic early positioning
- Players who buy during pre-round get **first access** before the chaos begins
- **Side bets** can be placed during this window for additional strategy
- **No selling** is allowed until the game becomes active
- This phase provides a **predictable entry point** for automated trading strategies
- **Complete database tracking** enables detailed participation analysis
- **Real-time caching** supports responsive user interfaces
- **Validation systems** ensure data integrity and proper game flow

This 10-second window represents the calm before the storm, where strategic players position themselves for the upcoming price action!