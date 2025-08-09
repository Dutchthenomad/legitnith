from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
import uuid
from datetime import datetime, timezone
import asyncio
import contextlib

# Socket.IO client (read-only)
import socketio

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection (env provided)
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app and router with /api prefix
app = FastAPI()
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("rugs-data-service")

########################################################
# Models
########################################################
class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str

class ConnectionState(BaseModel):
    connected: bool
    socket_id: Optional[str] = None
    last_event_at: Optional[datetime] = None
    since_connected_ms: Optional[int] = None

class LiveState(BaseModel):
    gameId: Optional[str] = None
    phase: Optional[str] = None
    active: Optional[bool] = None
    rugged: Optional[bool] = None
    price: Optional[float] = None
    tickCount: Optional[int] = None
    cooldownTimer: Optional[int] = None
    provablyFair: Optional[Dict[str, Any]] = None
    updatedAt: Optional[datetime] = None

########################################################
# Utility helpers & PRNG verification (Alea + drift)
########################################################

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

async def ensure_indexes():
    # Create indexes used by queries
    await db.game_state_snapshots.create_index([("gameId", 1), ("tickCount", -1)])
    await db.game_state_snapshots.create_index([("createdAt", -1)])
    await db.trades.create_index([("gameId", 1), ("tickIndex", 1)])
    await db.games.create_index([("id", 1)], unique=True)
    await db.games.create_index([("phase", 1)])
    await db.events.create_index([("type", 1), ("createdAt", -1)])
    await db.prng_tracking.create_index([("gameId", 1)], unique=True)

# ---- Alea seedrandom port (matches JS seedrandom default) ----
def _mash():
    n = 0xefc8249d

    def mash(data: str) -> float:
        nonlocal n
        for c in data:
            n += ord(c)
            h = 0.02519603282416938 * n
            n = int(h)
            h -= n
            h *= n
            n = int(h)
            h -= n
            n += int(h * 4294967296)
        return (n & 0xffffffff) * 2.3283064365386963e-10

    return mash

def seedrandom_alea(seed: str):
    mash = _mash()
    s0 = mash(' ')
    s1 = mash(' ')
    s2 = mash(' ')
    s0 -= mash(seed)
    if s0 < 0:
        s0 += 1
    s1 -= mash(seed)
    if s1 < 0:
        s1 += 1
    s2 -= mash(seed)
    if s2 < 0:
        s2 += 1
    c = 1

    def random():
        nonlocal s0, s1, s2, c
        t = 2091639 * s0 + c * 2.3283064365386963e-10
        s0 = s1
        s1 = s2
        s2 = t - int(t)
        c = int(t)
        return s2

    return random

# Drift & verify matching spec
RUG_PROB = 0.005
DRIFT_MIN = -0.02
DRIFT_MAX = 0.03
BIG_MOVE_CHANCE = 0.125
BIG_MOVE_MIN = 0.15
BIG_MOVE_MAX = 0.25
GOD_CANDLE_CHANCE = 0.00001
GOD_CANDLE_MOVE = 10.0
STARTING_PRICE = 1.0


def drift_price(price: float, rand_fn, version: str = 'v3') -> float:
    # God candle (only v3 and price threshold approx <=100x start)
    if version == 'v3' and rand_fn() < GOD_CANDLE_CHANCE and price <= 100 * STARTING_PRICE:
        return price * GOD_CANDLE_MOVE

    change = 0.0
    if rand_fn() < BIG_MOVE_CHANCE:
        move_size = BIG_MOVE_MIN + rand_fn() * (BIG_MOVE_MAX - BIG_MOVE_MIN)
        change = move_size if rand_fn() > 0.5 else -move_size
    else:
        drift = DRIFT_MIN + rand_fn() * (DRIFT_MAX - DRIFT_MIN)
        volatility = 0.005 * (min(10.0, price ** 0.5) if version != 'v1' else price ** 0.5)
        change = drift + (volatility * (2 * rand_fn() - 1))

    new_price = price * (1 + change)
    if new_price < 0:
        new_price = 0.0
    return new_price


def verify_game(server_seed: str, game_id: str, version: str = 'v3') -> Dict[str, Any]:
    combined_seed = f"{server_seed}-{game_id}"
    prng = seedrandom_alea(combined_seed)

    price = 1.0
    peak = 1.0
    rugged = False
    prices = [1.0]

    for tick in range(5000):
        if prng() < RUG_PROB:
            rugged = True
            break
        price = drift_price(price, prng, version)
        prices.append(price)
        if price > peak:
            peak = price

    return {
        "prices": prices,
        "peakMultiplier": peak,
        "rugged": rugged,
        "totalTicks": len(prices) - 1,
    }

async def run_prng_verification(game_id: str):
    # Gather server seed and version
    tracking = await db.prng_tracking.find_one({"gameId": game_id})
    game = await db.games.find_one({"id": game_id})
    if not tracking and not game:
        raise HTTPException(status_code=404, detail="game not found")

    server_seed = (tracking or {}).get("serverSeed") or (game or {}).get("serverSeed")
    server_seed_hash = (tracking or {}).get("serverSeedHash") or ((game or {}).get("serverSeedHash"))
    version = (tracking or {}).get("version") or (game or {}).get("version") or 'v3'

    if not server_seed:
        # Not ready
        await db.prng_tracking.update_one(
            {"gameId": game_id},
            {"$set": {"status": "AWAITING_SEED", "updatedAt": now_utc()}},
            upsert=True,
        )
        return {"status": "AWAITING_SEED"}

    # Determine expected arrays: prefer stored history on games
    expected_prices = None
    expected_peak = None
    if game and isinstance(game.get("history"), dict):
        hist = game["history"]
        expected_prices = hist.get("prices")
        expected_peak = hist.get("peakMultiplier") or hist.get("peak")

    if expected_prices is None:
        # fallback to the last snapshot with prices
        last_snap = await db.game_state_snapshots.find({"gameId": game_id}).sort("createdAt", -1).limit(1).to_list(1)
        if last_snap:
            expected_prices = (last_snap[0].get("payload") or {}).get("prices")
            expected_peak = (last_snap[0].get("payload") or {}).get("peakMultiplier")

    if not expected_prices:
        # Can't verify without expected
        await db.prng_tracking.update_one(
            {"gameId": game_id},
            {"$set": {"status": "MISSING_EXPECTED", "serverSeed": server_seed, "updatedAt": now_utc()}},
            upsert=True,
        )
        return {"status": "MISSING_EXPECTED"}

    # Compute verification
    verified = verify_game(server_seed, game_id, version)

    # Compare with tolerance
    def arrays_match(a, b, eps=1e-6):
        if len(a) != len(b):
            return False
        for i in range(len(a)):
            if abs(float(a[i]) - float(b[i])) > eps:
                return False
        return True

    match = arrays_match(expected_prices, verified["prices"]) and (
        expected_peak is None or abs(float(expected_peak) - float(verified["peakMultiplier"])) < 1e-6
    )

    result = {
        "gameId": game_id,
        "serverSeed": server_seed,
        "serverSeedHash": server_seed_hash,
        "version": version,
        "calculated": {
            "peakMultiplier": verified["peakMultiplier"],
            "totalTicks": verified["totalTicks"],
            "lastPrice": verified["prices"][-1],
            "length": len(verified["prices"]),
        },
        "expected": {
            "peakMultiplier": expected_peak,
            "totalTicks": len(expected_prices) - 1,
            "lastPrice": expected_prices[-1],
            "length": len(expected_prices),
        },
        "fullVerification": match,
        "verifiedAt": now_utc().isoformat(),
    }

    # Persist to tracking and games
    await db.prng_tracking.update_one(
        {"gameId": game_id},
        {
            "$set": {
                "serverSeed": server_seed,
                "status": "VERIFIED" if match else "FAILED",
                "verification": result,
                "updatedAt": now_utc(),
            }
        },
        upsert=True,
    )

    if game:
        await db.games.update_one(
            {"id": game_id},
            {"$set": {"prngVerified": bool(match), "prngVerificationData": result, "updatedAt": now_utc()}},
        )

    return result

########################################################
# Socket.IO Background Listener (read-only)
########################################################

SIO_URL = "https://backend.rugs.fun?frontend-version=1.0"

class RugsSocketService:
    def __init__(self, db):
        self.db = db
        self.sio = socketio.AsyncClient(reconnection=True)
        self.connected = False
        self.socket_id = None
        self.last_event_at: Optional[datetime] = None
        self.connected_at_ms: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._shutdown = False

        # runtime tracking
        self.current_game_id: Optional[str] = None
        self.game_stats: Dict[str, Dict[str, Any]] = {}

        # Bind handlers
        @self.sio.event
        async def connect():
            self.connected = True
            self.socket_id = self.sio.sid
            self.connected_at_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
            logger.info(f"Connected to Rugs.fun WebSocket as {self.socket_id}")
            await self._log_connection_event("CONNECTED", {"socketId": self.socket_id})

        @self.sio.event
        async def disconnect():
            logger.warning("Disconnected from Rugs.fun WebSocket")
            self.connected = False
            await self._log_connection_event("DISCONNECTED", {})

        @self.sio.event
        async def connect_error(data):
            logger.error(f"Connection error: {data}")
            await self._log_connection_event("ERROR", {"error": str(data)})

        # Core game events (read-only)
        @self.sio.on('gameStateUpdate')
        async def on_game_state(data):
            await self._handle_game_state_update(data)

        @self.sio.on('standard/newTrade')
        async def on_new_trade(trade):
            await self._handle_new_trade(trade)

        @self.sio.on('gameStatePlayerUpdate')
        async def on_player_update(payload):
            await self._store_event("gameStatePlayerUpdate", payload)

        @self.sio.on('rugPool')
        async def on_rug_pool(payload):
            await self._store_event("rugPool", payload)

        @self.sio.on('leaderboard')
        async def on_leaderboard(payload):
            await self._store_event("leaderboard", payload)

    def start(self):
        if self._task is None:
            self._shutdown = False
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._shutdown = True
        try:
            if self.sio.connected:
                await self.sio.disconnect()
        except Exception as e:
            logger.error(f"Error on disconnect: {e}")
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self):
        backoff = 1
        while not self._shutdown:
            try:
                logger.info("Attempting Socket.IO connection to Rugs.fun (read-only)...")
                await self.sio.connect(SIO_URL, transports=['websocket', 'polling'])
                await self.sio.wait()  # Blocks until disconnect
                backoff = 1  # reset after clean session
            except Exception as e:
                logger.error(f"Socket.IO loop error: {e}")
                await self._log_connection_event("ERROR", {"error": str(e)})
                await asyncio.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)

    async def _log_connection_event(self, event_type: str, metadata: Dict[str, Any]):
        doc = {
            "_id": str(uuid.uuid4()),
            "socketId": self.socket_id,
            "eventType": event_type,
            "metadata": metadata,
            "timestampMs": int(datetime.now(tz=timezone.utc).timestamp() * 1000),
            "createdAt": now_utc(),
        }
        await self.db.connection_events.insert_one(doc)

    async def _handle_game_state_update(self, data: Dict[str, Any]):
        self.last_event_at = now_utc()

        # Derive canonical phase
        phase = self._derive_phase(data)

        game_id = data.get("gameId")
        price = data.get("price") or 1.0
        tick_count = data.get("tickCount") or 0
        provably_fair = data.get("provablyFair") or {}
        version = provably_fair.get("version") or "v3"
        server_seed_hash = provably_fair.get("serverSeedHash")

        # Detect new active game
        if data.get("active") and (self.current_game_id != game_id):
            self.current_game_id = game_id
            self.game_stats[game_id] = {"peak": float(price), "ticks": int(tick_count)}

            # Record current game in meta for quick lookup
            await self.db.meta.update_one(
                {"key": "current_game_id"},
                {"$set": {"key": "current_game_id", "value": game_id, "updatedAt": now_utc()}},
                upsert=True,
            )

            # Upsert games record as ACTIVE start
            await self.db.games.update_one(
                {"id": game_id},
                {
                    "$setOnInsert": {
                        "id": game_id,
                        "startTime": now_utc(),
                        "createdAt": now_utc(),
                    },
                    "$set": {
                        "phase": "ACTIVE",
                        "version": version,
                        "serverSeedHash": server_seed_hash,
                        "lastSeenAt": now_utc(),
                    },
                },
                upsert=True,
            )

            # Initialize PRNG tracking scaffold
            if server_seed_hash:
                await self.db.prng_tracking.update_one(
                    {"gameId": game_id},
                    {
                        "$setOnInsert": {"createdAt": now_utc()},
                        "$set": {
                            "gameId": game_id,
                            "serverSeedHash": server_seed_hash,
                            "version": version,
                            "status": "TRACKING",
                            "updatedAt": now_utc(),
                        },
                    },
                    upsert=True,
                )

        # Update running peak and ticks for active game
        if game_id:
            stats = self.game_stats.get(game_id) or {"peak": 1.0, "ticks": 0}
            new_peak = stats["peak"]
            if isinstance(price, (float, int)) and price > stats["peak"]:
                new_peak = float(price)
            stats.update({"peak": new_peak, "ticks": int(tick_count)})
            self.game_stats[game_id] = stats

            # Persist rolling stats and phase
            try:
                await self.db.games.update_one(
                    {"id": game_id},
                    {
                        "$set": {
                            "peakMultiplier": stats["peak"],
                            "totalTicks": stats["ticks"],
                            "phase": "RUG" if phase == "RUG" else ("COOLDOWN" if phase == "COOLDOWN" else ("PRE_ROUND" if phase == "PRE_ROUND" else "ACTIVE" if phase == "ACTIVE" else "UNKNOWN")),
                            "version": version,
                            "serverSeedHash": server_seed_hash,
                            "lastSeenAt": now_utc(),
                        }
                    },
                    upsert=True,
                )
            except Exception as e:
                logger.error(f"Game rolling update error: {e}")

        # Insert snapshot (for observability)
        try:
            snap = {
                "_id": str(uuid.uuid4()),
                "gameId": game_id,
                "tickCount": tick_count,
                "active": data.get("active"),
                "rugged": data.get("rugged"),
                "price": price,
                "cooldownTimer": data.get("cooldownTimer"),
                "provablyFair": provably_fair,
                "phase": phase,
                "payload": data,
                "createdAt": now_utc(),
            }
            await self.db.game_state_snapshots.insert_one(snap)
        except Exception as e:
            logger.error(f"Snapshot insert error: {e}")

        # Upsert live state singleton (for HUD)
        try:
            lite = {
                "gameId": game_id,
                "active": data.get("active"),
                "rugged": data.get("rugged"),
                "price": price,
                "tickCount": tick_count,
                "cooldownTimer": data.get("cooldownTimer"),
                "provablyFair": provably_fair,
                "phase": phase,
                "updatedAt": now_utc(),
            }
            await self.db.meta.update_one({"key": "live_state"}, {"$set": {"key": "live_state", **lite}}, upsert=True)
        except Exception as e:
            logger.error(f"Live state upsert error: {e}")

        # If history supplied during cooldown, capture revealed server seeds for completed games & verify
        try:
            history = data.get("gameHistory")
            if isinstance(history, list):
                for g in history:
                    gid = g.get("id") or g.get("gameId")
                    if not gid:
                        continue
                    pf = (g.get("provablyFair") or {})
                    srv_seed = pf.get("serverSeed")
                    updates = {"id": gid, "history": g, "lastSeenAt": now_utc()}
                    if srv_seed:
                        updates.update({"serverSeed": srv_seed})
                        await self.db.prng_tracking.update_one(
                            {"gameId": gid},
                            {"$set": {"serverSeed": srv_seed, "status": "COMPLETE", "updatedAt": now_utc()}},
                            upsert=True,
                        )
                        # schedule verification
                        asyncio.create_task(run_prng_verification(gid))
                    await self.db.games.update_one({"id": gid}, {"$set": updates}, upsert=True)
        except Exception as e:
            logger.error(f"History upsert error: {e}")

        # RUG end detection: set endTime on current game if rugged
        if data.get("rugged") and game_id:
            try:
                await self.db.games.update_one({"id": game_id}, {"$set": {"endTime": now_utc(), "phase": "RUG", "lastSeenAt": now_utc()}})
            except Exception as e:
                logger.error(f"RUG end update error: {e}")

    async def _handle_new_trade(self, trade: Dict[str, Any]):
        self.last_event_at = now_utc()
        try:
            doc = {
                "_id": str(uuid.uuid4()),
                "eventId": str(trade.get("id")),
                "gameId": trade.get("gameId"),
                "playerId": trade.get("playerId"),
                "type": trade.get("type"),
                "qty": trade.get("qty"),
                "tickIndex": trade.get("tickIndex"),
                "coin": trade.get("coin"),
                "amount": trade.get("amount"),
                "price": trade.get("price"),
                "createdAt": now_utc(),
            }
            await self.db.trades.insert_one(doc)
        except Exception as e:
            logger.error(f"Trade insert error: {e}")

    async def _store_event(self, event_type: str, payload: Dict[str, Any]):
        self.last_event_at = now_utc()
        try:
            await self.db.events.insert_one({
                "_id": str(uuid.uuid4()),
                "type": event_type,
                "payload": payload,
                "createdAt": now_utc(),
            })
        except Exception as e:
            logger.error(f"Event store error: {e}")

    @staticmethod
    def _derive_phase(data: Dict[str, Any]) -> str:
        active = data.get("active")
        rugged = data.get("rugged")
        cooldown = data.get("cooldownTimer") or 0
        allow_pre = data.get("allowPreRoundBuys")
        if rugged:
            return "RUG"
        if active and not rugged:
            return "ACTIVE"
        if (not active) and cooldown and cooldown > 0:
            return "COOLDOWN"
        if (not active) and (cooldown == 0) and allow_pre:
            return "PRE_ROUND"
        return "UNKNOWN"

# Instance holder
auth_svc: Optional[RugsSocketService] = None

########################################################
# API Routes
########################################################

@api_router.get("/")
async def root():
    return {"message": "Hello World"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_obj = StatusCheck(**input.dict())
    await db.status_checks.insert_one({"_id": status_obj.id, **status_obj.model_dump()})
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    rows = await db.status_checks.find().sort("timestamp", -1).to_list(100)
    out = []
    for r in rows:
        r.pop("_id", None)
        out.append(StatusCheck(**r))
    return out

@api_router.get("/health")
async def health():
    return {"status": "ok", "time": now_utc().isoformat()}

@api_router.get("/connection", response_model=ConnectionState)
async def connection():
    global auth_svc
    if auth_svc is None:
        return ConnectionState(connected=False)
    since_ms = None
    if auth_svc.connected_at_ms:
        since_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000) - auth_svc.connected_at_ms
    return ConnectionState(
        connected=auth_svc.connected,
        socket_id=auth_svc.socket_id,
        last_event_at=auth_svc.last_event_at,
        since_connected_ms=since_ms,
    )

@api_router.get("/live", response_model=LiveState)
async def live_state():
    doc = await db.meta.find_one({"key": "live_state"})
    if not doc:
        return LiveState()
    doc.pop("_id", None)
    doc.pop("key", None)
    return LiveState(**doc)

@api_router.get("/snapshots")
async def snapshots(limit: int = 50):
    limit = max(1, min(limit, 200))
    rows = await db.game_state_snapshots.find({}, {"payload": 0}).sort("createdAt", -1).to_list(limit)
    for r in rows:
        r["id"] = r.pop("_id", None)
        if isinstance(r.get("createdAt"), datetime):
            r["createdAt"] = r["createdAt"].isoformat()
    return {"items": rows}

@api_router.get("/games")
async def games(limit: int = 50):
    limit = max(1, min(limit, 200))
    rows = await db.games.find().sort("lastSeenAt", -1).to_list(limit)
    for r in rows:
        r.pop("_id", None)
        if isinstance(r.get("lastSeenAt"), datetime):
            r["lastSeenAt"] = r["lastSeenAt"].isoformat()
        if isinstance(r.get("startTime"), datetime):
            r["startTime"] = r["startTime"].isoformat()
        if isinstance(r.get("endTime"), datetime):
            r["endTime"] = r["endTime"].isoformat()
    return {"items": rows}

@api_router.get("/games/current")
async def game_current():
    live = await db.meta.find_one({"key": "live_state"})
    if not live or not live.get("gameId"):
        return {}
    g = await db.games.find_one({"id": live["gameId"]})
    if not g:
        return {}
    g.pop("_id", None)
    # stringify dates
    for dkey in ["startTime", "endTime", "lastSeenAt", "createdAt", "updatedAt"]:
        if isinstance(g.get(dkey), datetime):
            g[dkey] = g[dkey].isoformat()
    return g

@api_router.get("/games/{game_id}")
async def game_by_id(game_id: str):
    g = await db.games.find_one({"id": game_id})
    if not g:
        raise HTTPException(status_code=404, detail="game not found")
    g.pop("_id", None)
    for dkey in ["startTime", "endTime", "lastSeenAt", "createdAt", "updatedAt"]:
        if isinstance(g.get(dkey), datetime):
            g[dkey] = g[dkey].isoformat()
    return g

@api_router.get("/prng/tracking")
async def prng_tracking(limit: int = 50):
    limit = max(1, min(limit, 200))
    rows = await db.prng_tracking.find().sort("updatedAt", -1).to_list(limit)
    for r in rows:
        r.pop("_id", None)
        if isinstance(r.get("createdAt"), datetime):
            r["createdAt"] = r["createdAt"].isoformat()
        if isinstance(r.get("updatedAt"), datetime):
            r["updatedAt"] = r["updatedAt"].isoformat()
    return {"items": rows}

@api_router.get("/games/{game_id}/verification")
async def game_verification(game_id: str):
    t = await db.prng_tracking.find_one({"gameId": game_id})
    if not t:
        raise HTTPException(status_code=404, detail="tracking not found")
    t.pop("_id", None)
    if isinstance(t.get("createdAt"), datetime):
        t["createdAt"] = t["createdAt"].isoformat()
    if isinstance(t.get("updatedAt"), datetime):
        t["updatedAt"] = t["updatedAt"].isoformat()
    return t

@api_router.post("/prng/verify/{game_id}")
async def trigger_verification(game_id: str):
    result = await run_prng_verification(game_id)
    return result

# Include router and CORS
app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

########################################################
# Lifespan hooks
########################################################

@app.on_event("startup")
async def startup_event():
    global auth_svc
    await ensure_indexes()
    auth_svc = RugsSocketService(db)
    auth_svc.start()
    logger.info("Rugs Socket Service started")

@app.on_event("shutdown")
async def shutdown_db_client():
    global auth_svc
    try:
        if auth_svc:
            await auth_svc.stop()
    finally:
        client.close()