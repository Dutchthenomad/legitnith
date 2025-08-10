# RUGS.FUN WebSocket Integration Technical Specification - Final Implementation

## Overview

This document provides the complete specification for connecting to Rugs.fun backend and handling real-time game data with production-ready database integration. It consolidates all critical WebSocket integration details, event structures, PRNG verification system, and complete data persistence patterns for building enterprise-grade rugs.fun applications.

---

## Connection Configuration

### WebSocket Server Details

```javascript
const socket = io('https://backend.rugs.fun?frontend-version=1.0', {
    reconnection: true,
    reconnectionAttempts: 5,
    transports: ['websocket', 'polling']
});
```

**Connection Parameters:**
- **Protocol**: Socket.io v4.x
- **Transport**: WebSocket with polling fallback
- **Authentication**: Not required for read-only access
- **Server**: `https://backend.rugs.fun?frontend-version=1.0`
- **Required Parameter**: `frontend-version=1.0` (mandatory as of August 2025)

### Critical Warnings

**⚠️ CRITICAL: Listen-Only Connection**

**DO NOT emit any events or send responses to the server**. The connection has anti-bot authentication mechanisms that will immediately block/error your connection if you attempt to:

- Emit any events (e.g., `socket.emit()`)
- Send acknowledgments
- Respond to server messages
- Attempt any bidirectional communication

**This is a READ-ONLY feed. Listen to events only. No responses.**

---

## PRNG System & Game Verification

### Game Parameters

- **Rug Probability**: 0.005 (0.5% per tick)
- **Drift Range**: -0.02 to 0.03
- **Big Move Chance**: 0.125 (12.5%)
- **Big Move Range**: 0.15 to 0.25
- **God Candle Chance**: 0.00001 (0.001%)
- **God Candle Move**: 10x multiplier

### Complete PRNG Source Code

```javascript
// Price drift calculation - matches server implementation exactly
function driftPrice(
    price,
    DRIFT_MIN,
    DRIFT_MAX,
    BIG_MOVE_CHANCE,
    BIG_MOVE_MIN,
    BIG_MOVE_MAX,
    randFn,
    version = 'v3',
    GOD_CANDLE_CHANCE = 0.00001,
    GOD_CANDLE_MOVE = 10.0,
    STARTING_PRICE = 1.0
) {
    // v3 adds God Candle feature - rare massive price increase
    if (version === 'v3' && randFn() < GOD_CANDLE_CHANCE && price <= 100 * STARTING_PRICE) {
        return price * GOD_CANDLE_MOVE;
    }
    
    let change = 0;
    
    if (randFn() < BIG_MOVE_CHANCE) {
        const moveSize = BIG_MOVE_MIN + randFn() * (BIG_MOVE_MAX - BIG_MOVE_MIN);
        change = randFn() > 0.5 ? moveSize : -moveSize;
    } else {
        const drift = DRIFT_MIN + randFn() * (DRIFT_MAX - DRIFT_MIN);
        
        // Version difference is in this volatility calculation
        const volatility = version === 'v1' 
            ? 0.005 * Math.sqrt(price)
            : 0.005 * Math.min(10, Math.sqrt(price));
            
        change = drift + (volatility * (2 * randFn() - 1));
    }
    
    let newPrice = price * (1 + change);

    if (newPrice < 0) {
        newPrice = 0;
    }

    return newPrice;
}

// Game verification function - verify complete games against server results
function verifyGame(serverSeed, gameId, version = 'v3') {
    const combinedSeed = serverSeed + '-' + gameId;
    const prng = new Math.seedrandom(combinedSeed);
    
    let price = 1.0;
    let peakMultiplier = 1.0;
    let rugged = false;
    const prices = [1.0];
    
    // Game constants
    const RUG_PROB = 0.005;
    const DRIFT_MIN = -0.02;
    const DRIFT_MAX = 0.03;
    const BIG_MOVE_CHANCE = 0.125;
    const BIG_MOVE_MIN = 0.15;
    const BIG_MOVE_MAX = 0.25;
    
    for (let tick = 0; tick < 5000 && !rugged; tick++) {
        // Check for rug event
        if (prng() < RUG_PROB) {
            rugged = true;
            continue;
        }
        
        // Calculate new price
        const newPrice = driftPrice(
            price,
            DRIFT_MIN, 
            DRIFT_MAX, 
            BIG_MOVE_CHANCE, 
            BIG_MOVE_MIN, 
            BIG_MOVE_MAX,
            prng.bind(prng),
            version
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
        totalTicks: prices.length - 1
    };
}
```

### Production PRNG Tracking System with Database Integration

```javascript
class ProductionPRNGGameTracker {
    constructor(dbConnection, cacheConnection) {
        this.db = dbConnection;
        this.cache = cacheConnection;
        
        this.trackedGameId = null;
        this.serverSeedHash = null;
        this.gameVersion = 'v3';
        
        // Real-time tracking
        this.livePrices = [];
        this.currentTick = 0;
        this.predictedPrices = [];
        
        // Verification data
        this.serverSeed = null;
        this.prngVerified = false;
    }

    async setTrackedGame(gameId, serverSeedHash, version = 'v3') {
        this.trackedGameId = gameId;
        this.serverSeedHash = serverSeedHash;
        this.gameVersion = version;
        this.livePrices = [1.0];
        this.currentTick = 0;
        this.prngVerified = false;
        
        console.log(`🎯 PRNG Tracker: Monitoring game ${gameId}`);
        console.log(`🔐 Server Seed Hash: ${serverSeedHash}`);

        // Store initial PRNG tracking record
        await this.initializePRNGTracking(gameId, serverSeedHash, version);
    }

    async initializePRNGTracking(gameId, serverSeedHash, version) {
        try {
            await this.db.query(`
                INSERT INTO prng_tracking (
                    game_id, server_seed_hash, version, status, created_at
                ) VALUES ($1, $2, $3, 'TRACKING', NOW())
                ON CONFLICT (game_id) DO UPDATE SET
                    server_seed_hash = EXCLUDED.server_seed_hash,
                    version = EXCLUDED.version,
                    status = EXCLUDED.status,
                    updated_at = NOW()
            `, [gameId, serverSeedHash, version]);

        } catch (error) {
            console.error(`❌ Failed to initialize PRNG tracking: ${error}`);
        }
    }

    async trackRealTimeTick(tickData) {
        // Verify game ID
        if (tickData.gameId !== this.trackedGameId) {
            return;
        }

        // Store real-time price data
        this.livePrices = [...tickData.prices];
        this.currentTick = tickData.tickCount;

        // Update PRNG tracking progress
        await this.updatePRNGProgress(tickData);

        // Log real-time progress
        console.log(`📊 Tick ${this.currentTick}: Price ${tickData.price.toFixed(6)}`);
    }

    async updatePRNGProgress(tickData) {
        try {
            await this.db.query(`
                UPDATE prng_tracking SET 
                    current_tick = $2,
                    current_price = $3,
                    live_prices = $4,
                    updated_at = NOW()
                WHERE game_id = $1
            `, [
                tickData.gameId, 
                tickData.tickCount, 
                tickData.price,
                JSON.stringify(this.livePrices)
            ]);

        } catch (error) {
            console.error(`❌ Failed to update PRNG progress: ${error}`);
        }
    }

    async onGameComplete(gameHistory) {
        // Extract server seed when revealed
        const ourGame = gameHistory.find(g => g.id === this.trackedGameId);
        
        if (!ourGame) {
            console.error('❌ Game not found in history for PRNG verification');
            return;
        }

        this.serverSeed = ourGame.provablyFair.serverSeed;
        
        // Verify the complete game using PRNG
        await this.verifyCompletedGame(ourGame);
    }

    async verifyCompletedGame(gameData) {
        if (!this.serverSeed) {
            console.error('❌ No server seed available for verification');
            return;
        }

        console.log('🔍 PRNG VERIFICATION STARTING...');
        console.log(`Game ID: ${gameData.id}`);
        console.log(`Server Seed: ${this.serverSeed}`);
        console.log(`Expected Peak: ${gameData.peakMultiplier}`);
        console.log(`Expected Ticks: ${gameData.prices.length}`);

        // Run PRNG verification
        const verified = verifyGame(this.serverSeed, gameData.id, this.gameVersion);

        // Compare results
        const verification = {
            gameId: gameData.id,
            serverSeed: this.serverSeed,
            serverSeedHash: this.serverSeedHash,
            
            // Peak multiplier comparison
            expectedPeak: gameData.peakMultiplier,
            calculatedPeak: verified.peakMultiplier,
            peakMatch: Math.abs(gameData.peakMultiplier - verified.peakMultiplier) < 0.000001,
            
            // Price array comparison
            expectedTicks: gameData.prices.length,
            calculatedTicks: verified.prices.length,
            ticksMatch: gameData.prices.length === verified.prices.length,
            
            // Final price comparison
            expectedFinalPrice: gameData.prices[gameData.prices.length - 1],
            calculatedFinalPrice: verified.prices[verified.prices.length - 1],
            
            // Complete verification
            fullVerification: this.comparePriceArrays(gameData.prices, verified.prices),
            
            // Metadata
            verifiedAt: new Date(),
            version: this.gameVersion
        };

        this.prngVerified = verification.peakMatch && verification.ticksMatch && verification.fullVerification;

        // Store verification results
        await this.storeVerificationResults(verification);

        if (this.prngVerified) {
            console.log('✅ PRNG VERIFICATION SUCCESSFUL!');
            console.log(`Peak Multiplier: ${verification.expectedPeak} ✓`);
            console.log(`Total Ticks: ${verification.expectedTicks} ✓`);
            console.log(`Price Arrays: MATCH ✓`);
        } else {
            console.error('❌ PRNG VERIFICATION FAILED!');
            console.error('Verification Details:', verification);
        }

        return verification;
    }

    async storeVerificationResults(verification) {
        try {
            // Update PRNG tracking record
            await this.db.query(`
                UPDATE prng_tracking SET 
                    server_seed = $2,
                    status = $3,
                    verification_results = $4,
                    verified_at = NOW(),
                    updated_at = NOW()
                WHERE game_id = $1
            `, [
                verification.gameId,
                verification.serverSeed,
                verification.fullVerification ? 'VERIFIED' : 'FAILED',
                JSON.stringify(verification)
            ]);

            // Update main games table
            await this.db.query(`
                UPDATE games SET 
                    prng_verified = $2,
                    prng_verification_data = $3,
                    updated_at = NOW()
                WHERE id = $1
            `, [
                verification.gameId,
                verification.fullVerification,
                JSON.stringify(verification)
            ]);

            console.log(`📊 PRNG verification results stored for: ${verification.gameId}`);

        } catch (error) {
            console.error(`❌ Failed to store verification results: ${error}`);
        }
    }

    comparePriceArrays(expected, calculated) {
        if (expected.length !== calculated.length) {
            return false;
        }

        for (let i = 0; i < expected.length; i++) {
            // Allow small floating-point differences
            if (Math.abs(expected[i] - calculated[i]) > 0.000001) {
                console.error(`Price mismatch at tick ${i}: ${expected[i]} vs ${calculated[i]}`);
                return false;
            }
        }

        return true;
    }

    // Predict future prices (theoretical - requires knowing current PRNG state)
    async predictNextTicks(currentTick, numTicks = 10) {
        if (!this.serverSeed) {
            console.warn('⚠️ Cannot predict without server seed');
            return null;
        }

        // Recreate PRNG state up to current tick
        const combinedSeed = this.serverSeed + '-' + this.trackedGameId;
        const prng = new Math.seedrandom(combinedSeed);
        
        // Fast-forward PRNG to current state
        let currentPrice = 1.0;
        for (let tick = 0; tick < currentTick; tick++) {
            // Skip rug checks and price calculations to advance PRNG state
            if (prng() < 0.005) break; // Would have rugged
            
            currentPrice = driftPrice(
                currentPrice, -0.02, 0.03, 0.125, 0.15, 0.25,
                prng.bind(prng), this.gameVersion
            );
        }

        // Generate predictions
        const predictions = [];
        for (let i = 0; i < numTicks; i++) {
            if (prng() < 0.005) {
                predictions.push({ tick: currentTick + i + 1, event: 'RUG' });
                break;
            }
            
            currentPrice = driftPrice(
                currentPrice, -0.02, 0.03, 0.125, 0.15, 0.25,
                prng.bind(prng), this.gameVersion
            );
            
            predictions.push({ 
                tick: currentTick + i + 1, 
                price: currentPrice,
                event: 'PRICE_UPDATE'
            });
        }

        // Store predictions for later verification
        await this.storePredictions(predictions);

        return predictions;
    }

    async storePredictions(predictions) {
        try {
            await this.db.query(`
                INSERT INTO prng_predictions (
                    game_id, current_tick, predictions, created_at
                ) VALUES ($1, $2, $3, NOW())
            `, [
                this.trackedGameId,
                this.currentTick,
                JSON.stringify(predictions)
            ]);

        } catch (error) {
            console.error(`❌ Failed to store predictions: ${error}`);
        }
    }
}
```

---

## Production WebSocket Integration

### Complete Production WebSocket Handler

```javascript
import { io } from 'socket.io-client';

class ProductionRugsWebSocketHandler {
    constructor(dbConnection, cacheConnection, config = {}) {
        this.db = dbConnection;
        this.cache = cacheConnection;
        this.config = {
            reconnectAttempts: 5,
            reconnectDelay: 1000,
            healthCheckInterval: 30000,
            metricsInterval: 10000,
            ...config
        };
        
        this.socket = null;
        this.connected = false;
        this.reconnectAttempts = 0;
        
        // Initialize all production systems
        this.prngTracker = new ProductionPRNGGameTracker(dbConnection, cacheConnection);
        this.gameStateMachine = new ProductionRugsGameStateMachine(dbConnection, cacheConnection);
        this.realTimeMonitor = new ProductionCompleteGameMonitor(dbConnection, cacheConnection);
        
        // Connection state
        this.connectionStartTime = null;
        this.lastEventTime = null;
        this.connectionId = null;
        this.sessionMetrics = {
            totalMessages: 0,
            totalTrades: 0,
            totalGames: 0,
            startTime: null
        };
    }

    async connect() {
        console.log('🔌 Connecting to Rugs.fun WebSocket...');
        
        this.socket = io('https://backend.rugs.fun?frontend-version=1.0', {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionAttempts: this.config.reconnectAttempts,
            reconnectionDelay: this.config.reconnectDelay,
            reconnectionDelayMax: 5000,
            timeout: 20000
        });

        this.setupEventHandlers();
        this.connectionStartTime = Date.now();
        this.sessionMetrics.startTime = this.connectionStartTime;
    }

    setupEventHandlers() {
        // Connection management
        this.socket.on('connect', () => {
            this.onConnect();
        });

        this.socket.on('disconnect', (reason) => {
            this.onDisconnect(reason);
        });

        this.socket.on('connect_error', (error) => {
            this.onConnectionError(error);
        });

        // Core game events
        this.socket.on('gameStateUpdate', async (data) => {
            await this.onGameStateUpdate(data);
        });

        this.socket.on('standard/newTrade', async (trade) => {
            await this.onNewTrade(trade);
        });

        this.socket.on('gameStatePlayerUpdate', async (data) => {
            await this.onPlayerUpdate(data);
        });

        // Additional events
        this.socket.on('rugPool', async (data) => {
            await this.onRugPoolUpdate(data);
        });

        this.socket.on('leaderboard', async (data) => {
            await this.onLeaderboardUpdate(data);
        });
    }

    async onConnect() {
        this.connected = true;
        this.reconnectAttempts = 0;
        this.connectionId = this.socket.id;
        
        const connectionTime = Date.now() - this.connectionStartTime;
        console.log(`✅ Connected to Rugs.fun in ${connectionTime}ms`);
        console.log(`📡 Socket ID: ${this.socket.id}`);
        
        // Log connection event
        await this.logConnectionEvent('CONNECTED', { connectionTime, socketId: this.socket.id });
        
        // Initialize monitoring systems for new connection
        await this.initializeProductionMonitoring();
    }

    async onDisconnect(reason) {
        this.connected = false;
        console.log(`❌ Disconnected from Rugs.fun: ${reason}`);
        
        // Log disconnection event
        await this.logConnectionEvent('DISCONNECTED', { reason });
        
        // Handle different disconnect reasons
        if (reason === 'io server disconnect') {
            console.log('🔄 Server initiated disconnect - will reconnect automatically');
        } else if (reason === 'transport close') {
            console.log('🔄 Transport closed - will reconnect automatically');
        }
    }

    async onConnectionError(error) {
        this.reconnectAttempts++;
        console.error(`❌ Connection error (attempt ${this.reconnectAttempts}):`, error.message);
        
        // Log connection error
        await this.logConnectionEvent('ERROR', { 
            error: error.message, 
            attempt: this.reconnectAttempts 
        });
        
        if (this.reconnectAttempts >= this.config.reconnectAttempts) {
            console.error('🚫 Max reconnection attempts reached');
            await this.logConnectionEvent('MAX_RECONNECTS_REACHED', { 
                maxAttempts: this.config.reconnectAttempts 
            });
        }
    }

    async onGameStateUpdate(data) {
        this.lastEventTime = Date.now();
        this.sessionMetrics.totalMessages++;
        
        // Log raw data periodically for debugging
        if (data.tickCount % 20 === 0) {
            console.log('📡 Raw Game State:', {
                gameId: data.gameId,
                active: data.active,
                rugged: data.rugged,
                price: data.price,
                tick: data.tickCount,
                cooldown: data.cooldownTimer
            });
        }

        // Process through all production systems
        await this.gameStateMachine.processUpdate(data);
        await this.realTimeMonitor.onGameStateUpdate(data);
        
        // PRNG tracking
        if (data.active && !data.rugged) {
            await this.prngTracker.trackRealTimeTick(data);
        }

        // Handle game transitions
        await this.handleGameTransitions(data);
        
        // Store raw game state periodically
        if (data.tickCount % 5 === 0) {
            await this.storeGameStateSnapshot(data);
        }
    }

    async onNewTrade(trade) {
        this.lastEventTime = Date.now();
        this.sessionMetrics.totalTrades++;
        
        console.log(`💰 Trade: ${trade.type.toUpperCase()}`, {
            player: trade.playerId.substring(0, 20) + '...',
            amount: trade.amount,
            tick: trade.tickIndex,
            gameId: trade.gameId
        });

        // Process trade through production systems
        await this.realTimeMonitor.onNewTrade(trade);
        
        // Store trade immediately
        await this.storeTrade(trade);
    }

    async onPlayerUpdate(data) {
        this.lastEventTime = Date.now();
        // Handle player-specific updates (leaderboard changes, etc.)
        await this.storePlayerUpdate(data);
    }

    async onRugPoolUpdate(data) {
        console.log('🏆 Rug Pool Update:', data);
        await this.storeRugPoolUpdate(data);
    }

    async onLeaderboardUpdate(data) {
        // Process leaderboard updates
        await this.storeLeaderboardUpdate(data);
    }

    async handleGameTransitions(data) {
        const currentState = await this.gameStateMachine.determineState(data);
        
        // Handle specific state transitions for PRNG tracking
        switch(currentState) {
            case 'ACTIVE_GAMEPLAY':
                if (!this.prngTracker.trackedGameId) {
                    await this.startTrackingNewGame(data);
                }
                break;
                
            case 'COOLDOWN':
                if (data.gameHistory && this.prngTracker.trackedGameId) {
                    await this.prngTracker.onGameComplete(data.gameHistory);
                    this.sessionMetrics.totalGames++;
                }
                break;
                
            case 'PRE_ROUND':
                // Prepare for next game
                await this.prepareForNextGame(data);
                break;
        }
    }

    async startTrackingNewGame(data) {
        console.log('🎮 NEW GAME DETECTED - Starting comprehensive production tracking');
        
        // Set tracked game for all monitoring systems
        this.realTimeMonitor.setTrackedGame(data.gameId);
        await this.prngTracker.setTrackedGame(
            data.gameId, 
            data.provablyFair?.serverSeedHash,
            data.provablyFair?.version || 'v3'
        );
        
        console.log(`🎯 All production systems tracking: ${data.gameId}`);
        
        // Log game start
        await this.logGameEvent(data.gameId, 'GAME_TRACKING_STARTED', data);
    }

    async prepareForNextGame(data) {
        // Next game preparation logic
        if (data.gameId !== this.prngTracker.trackedGameId) {
            console.log(`🔄 Next game prepared: ${data.gameId}`);
            await this.logGameEvent(data.gameId, 'NEXT_GAME_PREPARED', data);
        }
    }

    // Database storage methods
    async logConnectionEvent(eventType, metadata) {
        try {
            await this.db.query(`
                INSERT INTO connection_events (
                    socket_id, event_type, metadata, timestamp_ms, created_at
                ) VALUES ($1, $2, $3, $4, NOW())
            `, [this.connectionId, eventType, JSON.stringify(metadata), Date.now()]);

        } catch (error) {
            console.error(`❌ Failed to log connection event: ${error}`);
        }
    }

    async logGameEvent(gameId, eventType, data) {
        try {
            await this.db.query(`
                INSERT INTO game_events (
                    game_id, event_type, event_data, timestamp_ms, created_at
                ) VALUES ($1, $2, $3, $4, NOW())
            `, [gameId, eventType, JSON.stringify(data), Date.now()]);

        } catch (error) {
            console.error(`❌ Failed to log game event: ${error}`);
        }
    }

    async storeGameStateSnapshot(data) {
        try {
            await this.db.query(`
                INSERT INTO game_state_snapshots (
                    game_id, tick_number, state_data, timestamp_ms, created_at
                ) VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (game_id, tick_number) DO UPDATE SET
                    state_data = EXCLUDED.state_data,
                    timestamp_ms = EXCLUDED.timestamp_ms
            `, [data.gameId, data.tickCount, JSON.stringify(data), Date.now()]);

        } catch (error) {
            console.error(`❌ Failed to store game state snapshot: ${error}`);
        }
    }

    async storeTrade(trade) {
        try {
            await this.db.query(`
                INSERT INTO trades (
                    id, game_id, player_id, type, tick_index, amount, 
                    quantity, price, coin, timestamp_ms, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    amount = EXCLUDED.amount,
                    quantity = EXCLUDED.quantity,
                    timestamp_ms = EXCLUDED.timestamp_ms
            `, [
                trade.id, trade.gameId, trade.playerId, trade.type,
                trade.tickIndex, trade.amount, trade.qty, 
                trade.price || null, trade.coin, Date.now()
            ]);

        } catch (error) {
            console.error(`❌ Failed to store trade: ${error}`);
        }
    }

    async storePlayerUpdate(data) {
        try {
            await this.db.query(`
                INSERT INTO player_updates (
                    update_data, timestamp_ms, created_at
                ) VALUES ($1, $2, NOW())
            `, [JSON.stringify(data), Date.now()]);

        } catch (error) {
            console.error(`❌ Failed to store player update: ${error}`);
        }
    }

    async storeRugPoolUpdate(data) {
        try {
            await this.db.query(`
                INSERT INTO rug_pool_updates (
                    pool_data, timestamp_ms, created_at
                ) VALUES ($1, $2, NOW())
            `, [JSON.stringify(data), Date.now()]);

        } catch (error) {
            console.error(`❌ Failed to store rug pool update: ${error}`);
        }
    }

    async storeLeaderboardUpdate(data) {
        try {
            await this.db.query(`
                INSERT INTO leaderboard_updates (
                    leaderboard_data, timestamp_ms, created_at
                ) VALUES ($1, $2, NOW())
            `, [JSON.stringify(data), Date.now()]);

        } catch (error) {
            console.error(`❌ Failed to store leaderboard update: ${error}`);
        }
    }

    // Health monitoring
    startHealthMonitoring() {
        setInterval(async () => {
            await this.checkConnectionHealth();
        }, this.config.healthCheckInterval);
    }

    async checkConnectionHealth() {
        if (!this.connected) {
            console.warn('⚠️ Connection lost');
            return;
        }

        const timeSinceLastEvent = Date.now() - this.lastEventTime;
        if (timeSinceLastEvent > 60000) { // No events for 1 minute
            console.warn(`⚠️ No events received for ${timeSinceLastEvent}ms`);
            await this.logConnectionEvent('NO_EVENTS_WARNING', { timeSinceLastEvent });
        }

        // Log connection health
        const health = {
            connected: this.connected,
            socketId: this.socket?.id,
            timeSinceLastEvent: timeSinceLastEvent + 'ms',
            currentGame: this.prngTracker.trackedGameId,
            sessionMetrics: this.sessionMetrics
        };

        console.log('💓 Connection Health:', health);
        
        // Store health metrics
        await this.storeHealthMetrics(health);
    }

    async storeHealthMetrics(health) {
        try {
            await this.db.query(`
                INSERT INTO connection_health (
                    socket_id, health_data, timestamp_ms, created_at
                ) VALUES ($1, $2, $3, NOW())
            `, [this.connectionId, JSON.stringify(health), Date.now()]);

        } catch (error) {
            console.error(`❌ Failed to store health metrics: ${error}`);
        }
    }

    async initializeProductionMonitoring() {
        // Start health monitoring
        this.startHealthMonitoring();
        
        // Initialize performance monitoring
        this.startPerformanceMonitoring();
    }

    startPerformanceMonitoring() {
        setInterval(async () => {
            const metrics = this.realTimeMonitor.getCompleteMetrics();
            if (metrics && metrics.isActive) {
                console.log('📊 Production Performance Metrics:', {
                    gameId: metrics.gameId,
                    currentTick: metrics.tick?.currentTick,
                    currentPrice: metrics.price?.current?.toFixed(6),
                    totalBuys: metrics.trades?.buys?.totalBuys,
                    totalSells: metrics.trades?.sells?.totalSells,
                    prngVerified: this.prngTracker.prngVerified,
                    sessionTotalMessages: this.sessionMetrics.totalMessages,
                    sessionTotalTrades: this.sessionMetrics.totalTrades
                });
                
                // Store performance metrics
                await this.storePerformanceMetrics(metrics);
            }
        }, this.config.metricsInterval);
    }

    async storePerformanceMetrics(metrics) {
        try {
            await this.db.query(`
                INSERT INTO performance_metrics (
                    game_id, metrics_data, session_metrics, timestamp_ms, created_at
                ) VALUES ($1, $2, $3, $4, NOW())
            `, [
                metrics.gameId, 
                JSON.stringify(metrics), 
                JSON.stringify(this.sessionMetrics),
                Date.now()
            ]);

        } catch (error) {
            console.error(`❌ Failed to store performance metrics: ${error}`);
        }
    }

    disconnect() {
        if (this.socket) {
            console.log('🔌 Manually disconnecting from Rugs.fun');
            this.socket.disconnect();
        }
    }

    // Get comprehensive session statistics
    getSessionStats() {
        const sessionDuration = Date.now() - this.sessionMetrics.startTime;
        
        return {
            connected: this.connected,
            sessionDuration: sessionDuration,
            connectionId: this.connectionId,
            ...this.sessionMetrics,
            messagesPerSecond: this.sessionMetrics.totalMessages / (sessionDuration / 1000),
            tradesPerSecond: this.sessionMetrics.totalTrades / (sessionDuration / 1000)
        };
    }
}
```

## Database Schema for WebSocket Integration

### Connection and Event Tracking Tables
```sql
-- Connection events
CREATE TABLE connection_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    socket_id VARCHAR(128),
    event_type VARCHAR(50) NOT NULL,
    metadata JSONB,
    timestamp_ms BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Game state snapshots
CREATE TABLE game_state_snapshots (
    game_id VARCHAR(64) NOT NULL REFERENCES games(id),
    tick_number INTEGER NOT NULL,
    state_data JSONB NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (game_id, tick_number)
);

-- PRNG tracking
CREATE TABLE prng_tracking (
    game_id VARCHAR(64) PRIMARY KEY REFERENCES games(id),
    server_seed_hash VARCHAR(128) NOT NULL,
    server_seed VARCHAR(128),
    version VARCHAR(10) NOT NULL,
    status VARCHAR(20) DEFAULT 'TRACKING',
    current_tick INTEGER DEFAULT 0,
    current_price DECIMAL(20,12) DEFAULT 1.0,
    live_prices JSONB,
    verification_results JSONB,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- PRNG predictions
CREATE TABLE prng_predictions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id VARCHAR(64) NOT NULL REFERENCES games(id),
    current_tick INTEGER NOT NULL,
    predictions JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Connection health monitoring
CREATE TABLE connection_health (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    socket_id VARCHAR(128),
    health_data JSONB NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance metrics
CREATE TABLE performance_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id VARCHAR(64) REFERENCES games(id),
    metrics_data JSONB NOT NULL,
    session_metrics JSONB,
    timestamp_ms BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Event Structure Reference

### Core Events

#### gameStateUpdate
```javascript
{
  "gameId": "20250807-300ad2bc3a224bf3",
  "active": true,
  "rugged": false,
  "price": 1.3222800991643695,
  "prices": [1, 1.018, 1.045, ...],
  "tickCount": 29,
  "cooldownTimer": 0,
  "allowPreRoundBuys": false,
  "leaderboard": [...],
  "provablyFair": {
    "serverSeedHash": "a64cf432b36edfba9bd856d8053b6396613fbf66f413ed8da9330bdefb589a6c",
    "version": "v3"
  }
}
```

#### standard/newTrade
```javascript
{
  "id": "40",
  "gameId": "20250807-300ad2bc3a224bf3",
  "playerId": "did:privy:cmaq2kqhg03c0jp0lvlk0dmcn",
  "type": "buy",  // or "sell"
  "qty": 0.21130086,
  "tickIndex": 26,
  "coin": "solana",
  "amount": 0.268,
  "price": 1.3554870894378277  // Only for sell events
}
```

---

## Production Usage Example

```javascript
// Initialize the production WebSocket handler
const dbConnection = await createDatabaseConnection();
const cacheConnection = await createCacheConnection();

const handler = new ProductionRugsWebSocketHandler(dbConnection, cacheConnection, {
    reconnectAttempts: 10,
    healthCheckInterval: 30000,
    metricsInterval: 5000
});

// Connect and start monitoring
await handler.connect();

// Monitor session statistics
setInterval(() => {
    const stats = handler.getSessionStats();
    console.log('📊 Session Statistics:', stats);
}, 60000); // Every minute

// Graceful shutdown
process.on('SIGINT', async () => {
    console.log('👋 Shutting down gracefully...');
    
    // Save final session metrics
    const finalStats = handler.getSessionStats();
    await handler.storePerformanceMetrics(finalStats);
    
    handler.disconnect();
    await dbConnection.end();
    await cacheConnection.quit();
    
    process.exit(0);
});
```

---

## Integration with Documentation Suite

This technical specification integrates with our complete documentation suite:

### **Phase Detection**
- **[Phase 1: RUG EVENT](rug-event-detection.md)** - Use for rug detection and game completion
- **[Phase 2: COOLDOWN](cooldown-phase-detection.md)** - Extract server seeds for PRNG verification
- **[Phase 3: PRE-ROUND](preround-phase-detection.md)** - Prepare for next game tracking
- **[Phase 4: ACTIVE GAMEPLAY](active-gameplay-phase-detection.md)** - Start real-time monitoring

### **Real-Time Monitoring**
- **[Real-Time Data Monitoring](realtime-data-monitoring.md)** - Complete data collection system
- **[Complete Integration Guide](rugs-fun-complete-guide.md)** - Master implementation guide

### **Data Architecture**
- **[Data Architecture & Management Framework](rugs-data-architecture.md)** - Production database design

---

## Production Implementation Checklist

### ✅ Connection Setup
- [ ] Socket.io v4.x client installed
- [ ] Correct server URL with `frontend-version=1.0`
- [ ] Listen-only configuration (no emits)
- [ ] Reconnection logic implemented
- [ ] Health monitoring active
- [ ] Database connection pool configured

### ✅ Game Tracking
- [ ] Game ID tracking from birth to death
- [ ] Phase detection system integrated
- [ ] Real-time tick monitoring active
- [ ] PRNG verification system ready
- [ ] All data persisted to database

### ✅ Data Integrity
- [ ] All monitoring systems use game ID verification
- [ ] Error handling for disconnections
- [ ] Data validation on all incoming events
- [ ] Performance monitoring active
- [ ] Database transactions for consistency

### ✅ PRNG Verification
- [ ] Server seed extraction during cooldown
- [ ] Complete game verification against PRNG
- [ ] Price array comparison implemented
- [ ] Verification logging active
- [ ] Results stored in database

### ✅ Production Readiness
- [ ] Database schema deployed
- [ ] Monitoring dashboards configured
- [ ] Alerting system active
- [ ] Backup procedures tested
- [ ] Performance benchmarks established

---

## Best Practices

### Connection Management
1. **Always include** `frontend-version=1.0` parameter
2. **Never emit** any events back to the server
3. **Implement robust** reconnection logic with exponential backoff
4. **Monitor connection health** with periodic checks
5. **Store all events** in database for analysis

### Game Tracking
1. **Track complete game lifecycles** from start to finish
2. **Verify all data** against tracked game IDs
3. **Extract server seeds** during cooldown phase for verification
4. **Implement PRNG verification** for transparency
5. **Store complete audit trail** in database

### Performance
1. **Use efficient data structures** for real-time processing
2. **Batch operations** where possible to reduce database load
3. **Monitor memory usage** for long-running applications
4. **Log performance metrics** for optimization
5. **Implement database connection pooling**

### Error Handling
1. **Gracefully handle** network disconnections
2. **Validate all incoming data** before processing
3. **Log errors** with sufficient context for debugging
4. **Implement circuit breakers** for critical failures
5. **Store error events** for analysis

---

## Conclusion

This production-ready technical specification provides everything needed to connect to the rugs.fun WebSocket feed and implement comprehensive game tracking with complete database integration and PRNG verification. By following these patterns and integrating with our complete documentation suite, developers can build robust, enterprise-grade applications that maintain perfect data integrity while providing deep insights into game mechanics.

The combination of real-time monitoring, phase detection, PRNG verification, and complete data persistence creates a bulletproof foundation for any rugs.fun application with full production-ready capabilities.