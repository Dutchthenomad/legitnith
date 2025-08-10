from fastapi import FastAPI, APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict, Set, Tuple, Callable
from collections import deque
import uuid
import time
from datetime import datetime, timezone
import asyncio
import contextlib
import json

# Socket.IO client (read-only)
import socketio

# JSON Schema validation
try:
    import fastjsonschema
except Exception:
    fastjsonschema = None

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection (env provided) with validation and optional timeouts
MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME')
if not MONGO_URL or not DB_NAME:
    raise RuntimeError("Missing required environment variables: MONGO_URL and/or DB_NAME")
# Optional Mongo timeouts (ms)
try:
    _ss = int(os.environ.get('MONGO_SERVER_SELECTION_TIMEOUT_MS', '5000'))
    _ct = int(os.environ.get('MONGO_CONNECT_TIMEOUT_MS', '5000'))
    _st = int(os.environ.get('MONGO_SOCKET_TIMEOUT_MS', '10000'))
except Exception:
    _ss, _ct, _st = 5000, 5000, 10000
client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=_ss, connectTimeoutMS=_ct, socketTimeoutMS=_st)
db = client[DB_NAME]

SCHEMA_DIR = ROOT_DIR.parent / "docs" / "ws-schema"

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
# JSON Schema registry & resolver (fastjsonschema)
########################################################

class SchemaRegistry:
    def __init__(self, schema_dir: Path):
        self.schema_dir = schema_dir
        self._raw: Dict[str, Dict[str, Any]] = {}
        self._resolved: Dict[str, Dict[str, Any]] = {}
        self._validators: Dict[str, Any] = {}
        self._descriptors: Dict[str, Dict[str, Any]] = {}
        self._inbound_to_key: Dict[str, str] = {
            "gameStateUpdate": "gameStateUpdate",
            "standard/newTrade": "newTrade",
            "gameStatePlayerUpdate": "gameStatePlayerUpdate",
            # side-bets
            "standard/sideBetPlaced": "currentSideBet",
            "standard/sideBetResult": "newSideBet",
            "sideBet": "newSideBet",
            # optional playerUpdate if received
            "playerUpdate": "playerUpdate",
        }
        self._outbound_mapping: Dict[str, str] = {
            "gameStateUpdate": "game_state_update",
            "newTrade": "trade",
            "currentSideBet": "side_bet",
            "newSideBet": "side_bet",
            "gameStatePlayerUpdate": "game_state_player_update",
            "playerUpdate": "player_update",
        }
        try:
            self._load_all()
        except Exception as e:
            logger.warning(f"SchemaRegistry initialization warning: {e}")

    def _load_all(self):
        if not self.schema_dir.exists():
            logger.warning(f"Schema directory not found: {self.schema_dir}")
            return
        # load raw
        for p in self.schema_dir.glob("*.json"):
            try:
                with open(p, "r") as f:
                    self._raw[p.name] = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load schema {p}: {e}")
        # resolve and compile
        for fname, schema in self._raw.items():
            try:
                resolved = self._resolve_refs(schema)
                self._resolved[fname] = resolved
                if fastjsonschema:
                    validator = fastjsonschema.compile(resolved)
                else:
                    validator = None
                key = fname.replace('.json', '')
                self._validators[key] = validator
                # descriptor: collect basic property types and required
                props = {}
                if isinstance(resolved.get("properties"), dict):
                    for k, v in resolved["properties"].items():
                        if isinstance(v, dict):
                            t = v.get("type")
                            if isinstance(t, list):
                                # choose first non-null
                                t = next((x for x in t if x != "null"), t[0] if t else None)
                            props[k] = {"type": t}
                self._descriptors[key] = {
                    "id": resolved.get("$id") or fname,
                    "title": resolved.get("title") or key,
                    "required": resolved.get("required", []),
                    "properties": props,
                    "outboundType": self._outbound_mapping.get(key),
                }
            except Exception as e:
                logger.warning(f"Failed to compile schema {fname}: {e}")

    def _resolve_refs(self, schema: Any, base_doc: Optional[Dict[str, Any]] = None) -> Any:
        """Resolve $ref supporting relative file refs with JSON Pointer fragments."""
        if isinstance(schema, dict):
            if "$ref" in schema and isinstance(schema["$ref"], str):
                ref = schema["$ref"]
                # Split file and fragment
                if "#" in ref:
                    file_part, frag = ref.split("#", 1)
                else:
                    file_part, frag = ref, ""
                target_doc: Optional[Dict[str, Any]] = None
                if file_part in ("", None):
                    target_doc = base_doc or schema
                else:
                    # load referenced file
                    ref_path = (self.schema_dir / file_part)
                    if not ref_path.exists():
                        # try direct name in _raw
                        ref_json = self._raw.get(file_part)
                        if ref_json is None:
                            raise FileNotFoundError(f"Ref file not found: {file_part}")
                        target_doc = ref_json
                    else:
                        with open(ref_path, "r") as f:
                            target_doc = json.load(f)
                target_doc = self._resolve_refs(target_doc, target_doc)
                # Resolve JSON Pointer fragment
                node: Any = target_doc
                if frag:
                    # remove leading '/'
                    pointer = frag[1:] if frag.startswith("/") else frag
                    if pointer:
                        for token in pointer.split("/"):
                            token = token.replace("~1", "/").replace("~0", "~")
                            if isinstance(node, dict) and token in node:
                                node = node[token]
                            else:
                                raise KeyError(f"Invalid $ref pointer {ref}")
                return self._resolve_refs(node, target_doc)
            else:
                return {k: self._resolve_refs(v, base_doc or schema) for k, v in schema.items()}
        elif isinstance(schema, list):
            return [self._resolve_refs(x, base_doc) for x in schema]
        else:
            return schema

    def validate(self, key: str, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        validator = self._validators.get(key)
        if not validator:
            return True, None
        try:
            validator(payload)
            return True, None
        except Exception as e:
            return False, str(e)

    def validate_inbound(self, inbound_event: str, payload: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str]]:
        key = self._inbound_to_key.get(inbound_event)
        if not key:
            return True, None, None
        ok, err = self.validate(key, payload)
        return ok, err, key

    def describe(self) -> Dict[str, Any]:
        items = []
        for key, desc in self._descriptors.items():
            items.append({"key": key, **desc})
        # only return stable order by key
        items.sort(key=lambda x: x["key"])
        return {"items": items}

schema_registry: Optional[SchemaRegistry] = None

########################################################
# Utility helpers & PRNG verification (Alea + drift)
########################################################

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

async def ensure_indexes():
    """Ensure all collection indexes exist for performance and data integrity."""
    # Observability snapshots: 10d TTL
    await db.game_state_snapshots.create_index([("gameId", 1), ("tickCount", -1)])
    await db.game_state_snapshots.create_index([("createdAt", -1)])
    try:
        await db.game_state_snapshots.create_index(
            [("createdAt", 1)],
            expireAfterSeconds=864000,
            name="snapshots_ttl_10d",
        )
    except Exception:
        try:
            await db.command({"collMod": "game_state_snapshots", "index": {"name": "snapshots_ttl_10d", "expireAfterSeconds": 864000}})
        except Exception as e:
            logger.warning(f"snapshots TTL collMod warn: {e}")

    # Trades
    await db.trades.create_index([("gameId", 1), ("tickIndex", 1)])

    # Games: analysis-friendly indexes
    await db.games.create_index([("id", 1)], unique=True)
    await db.games.create_index([("phase", 1)])
    await db.games.create_index([("hasGodCandle", 1)])
    await db.games.create_index([("prngVerified", 1)])
    await db.games.create_index([("startTime", -1)])
    await db.games.create_index([("endTime", -1)])
    await db.games.create_index([("rugTick", -1)])
    await db.games.create_index([("endPrice", -1)])
    await db.games.create_index([("peakMultiplier", -1)])
    await db.games.create_index([("totalTicks", -1)])

    # Side bets
    await db.side_bets.create_index([("gameId", 1), ("createdAt", -1)])
    if "startTick" in (await db.side_bets.find_one() or {}):
        await db.side_bets.create_index([("gameId", 1), ("startTick", 1)])

    # Trades: ensure idempotency by unique eventId when available
    try:
        await db.trades.create_index([("eventId", 1)], unique=True, name="uniq_eventId")
    except Exception:
        # fallback non-unique index to avoid full scans
        await db.trades.create_index([("eventId", 1)], name="idx_eventId")

    # Meta as a KV store
    try:
        await db.meta.create_index([("key", 1)], unique=True, name="uniq_key")
    except Exception:
        await db.meta.create_index([("key", 1)], name="idx_key")

    # Status checks
    await db.status_checks.create_index([("timestamp", -1)])


    # Events (optional TTL 30d)
    await db.events.create_index([("type", 1), ("createdAt", -1)])
    try:
        await db.events.create_index([("createdAt", 1)], expireAfterSeconds=2592000, name="events_ttl_30d")
    except Exception:
        try:
            await db.command({"collMod": "events", "index": {"name": "events_ttl_30d", "expireAfterSeconds": 2592000}})
        except Exception as e:
            logger.warning(f"events TTL collMod warn: {e}")

    # Connection events (optional TTL 30d)
    await db.connection_events.create_index([("eventType", 1), ("createdAt", -1)])
    try:
        await db.connection_events.create_index([("createdAt", 1)], expireAfterSeconds=2592000, name="conn_events_ttl_30d")
    except Exception:
        try:
            await db.command({"collMod": "connection_events", "index": {"name": "conn_events_ttl_30d", "expireAfterSeconds": 2592000}})
        except Exception as e:
            logger.warning(f"conn events TTL collMod warn: {e}")

    # PRNG tracking & god candles
    await db.prng_tracking.create_index([("gameId", 1)], unique=True)
    # Unique per (gameId, tickIndex) to avoid duplicate records for same tick
    try:
        await db.god_candles.create_index([("gameId", 1), ("tickIndex", 1)], unique=True, name="uniq_game_tick")
    except Exception:
        await db.god_candles.create_index([("gameId", 1), ("tickIndex", 1)], name="idx_game_tick")
    await db.god_candles.create_index([("createdAt", -1)])
    await db.god_candles.create_index([("underCap", 1)])

    # Ticks and OHLC indices
    await db.game_ticks.create_index([("gameId", 1), ("tick", 1)], unique=True)
    await db.game_indices.create_index([("gameId", 1), ("index", 1)], unique=True)
    await db.game_indices.create_index([("updatedAt", -1)])

# ---- Alea seedrandom port ----

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
    tracking = await db.prng_tracking.find_one({"gameId": game_id})
    game = await db.games.find_one({"id": game_id})
    if not tracking and not game:
        raise HTTPException(status_code=404, detail="game not found")

    server_seed = (tracking or {}).get("serverSeed") or (game or {}).get("serverSeed")
    server_seed_hash = (tracking or {}).get("serverSeedHash") or ((game or {}).get("serverSeedHash"))
    version = (tracking or {}).get("version") or (game or {}).get("version") or 'v3'

    if not server_seed:
        await db.prng_tracking.update_one(
            {"gameId": game_id},
            {"$set": {"status": "AWAITING_SEED", "updatedAt": now_utc()}},
            upsert=True,
        )
        return {"status": "AWAITING_SEED"}

    # Determine expected arrays
    expected_prices = None
    expected_peak = None
    if game and isinstance(game.get("history"), dict):
        hist = game["history"]
        expected_prices = hist.get("prices")
        expected_peak = hist.get("peakMultiplier") or hist.get("peak")

    if expected_prices is None:
        last_snap = await db.game_state_snapshots.find({"gameId": game_id}).sort("createdAt", -1).limit(1).to_list(1)
        if last_snap:
            expected_prices = (last_snap[0].get("payload") or {}).get("prices")
            expected_peak = (last_snap[0].get("payload") or {}).get("peakMultiplier")

    if not expected_prices:
        await db.prng_tracking.update_one(
            {"gameId": game_id},
            {"$set": {"status": "MISSING_EXPECTED", "serverSeed": server_seed, "updatedAt": now_utc()}},
            upsert=True,
        )
        return {"status": "MISSING_EXPECTED"}

    verified = verify_game(server_seed, game_id, version)

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
# Broadcaster for downstream consumers (WebSocket /api/ws/stream)
########################################################
class Broadcaster:
    def __init__(self):
        self.connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.connections.add(ws)

    async def unregister(self, ws: WebSocket):
        async with self._lock:
            if ws in self.connections:
                self.connections.remove(ws)

    async def broadcast(self, message: Dict[str, Any], send_timeout: float = 1.0):
        # Snapshot connections under lock
        async with self._lock:
            targets = list(self.connections)
        if not targets:
            return
        dead: List[WebSocket] = []

        async def send_one(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=send_timeout)
            except Exception:
                dead.append(ws)

        # Send concurrently outside the lock
        await asyncio.gather(*(send_one(ws) for ws in targets), return_exceptions=True)

        # Remove dead connections
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self.connections:
                        self.connections.discard(ws)
            # metrics hook for slow/broken clients
            try:
                metrics.incr_ws_drop(len(dead))
            except Exception:
                pass

broadcaster = Broadcaster()

# -------------------- In-memory metrics (lightweight) --------------------
class Metrics:
    def __init__(self):
        self.start_time = time.time()
        self.total_messages = 0
        self.total_trades = 0
        self.total_games_seen: Set[str] = set()
        self.error_counts: Dict[str, int] = {}
        self.msg_times = deque(maxlen=600)  # ~10 minutes if 1s buckets
        self.last_event_at: Optional[datetime] = None
        self.last_error_at: Optional[datetime] = None
        # schema validation counters
        self.schema_validation: Dict[str, Any] = {
            "total": 0,
            "perEvent": {}
        }
        # broadcaster metrics
        self.ws_slow_client_drops = 0
        # db ping metrics
        self.last_db_ping_ms: Optional[int] = None

    def incr_message(self):
        self.total_messages += 1
        now_s = int(time.time())
        self.msg_times.append(now_s)
        self.last_event_at = now_utc()

    def incr_trade(self):
        self.total_trades += 1

    def add_game(self, gid: Optional[str]):
        if gid:
            self.total_games_seen.add(gid)

    def incr_error(self, key: str):
        self.error_counts[key] = self.error_counts.get(key, 0) + 1
        self.last_error_at = now_utc()

    def incr_ws_drop(self, n: int = 1):
        self.ws_slow_client_drops += int(n)

    def msgs_per_sec_window(self, window_seconds: int = 60) -> float:
        if not self.msg_times:
            return 0.0
        now_s = int(time.time())
        count = sum(1 for t in self.msg_times if now_s - t < window_seconds)
        return count / float(window_seconds)

    def incr_schema(self, event_key: str, ok: bool):
        self.schema_validation["total"] += 1
        per = self.schema_validation["perEvent"].setdefault(event_key, {"ok": 0, "fail": 0})
        if ok:
            per["ok"] += 1
        else:
            per["fail"] += 1

metrics = Metrics()

########################################################
# Socket.IO Background Listener (read-only) + compaction + quality flags
########################################################

SIO_URL = os.environ.get("RUGS_UPSTREAM_URL", "https://backend.rugs.fun?frontend-version=1.0")

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
        # game_stats[gid]: {peak, ticks, last_price, last_tick, god_candle_seen, quality}

        @self.sio.event
        async def connect():
            self.connected = True
            self.socket_id = self.sio.sid
            self.connected_at_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
            logger.info(f"Connected to Rugs.fun WebSocket as {self.socket_id}")
            try:
                await self._log_connection_event("CONNECTED", {"socketId": self.socket_id})
            except Exception:
                metrics.incr_error("connection_log_error")

        @self.sio.event
        async def disconnect():
            logger.warning("Disconnected from Rugs.fun WebSocket")
            self.connected = False
            try:
                await self._log_connection_event("DISCONNECTED", {})
            except Exception:
                metrics.incr_error("connection_log_error")

        @self.sio.event
        async def connect_error(data):
            logger.error(f"Connection error: {data}")
            metrics.incr_error("socket_connect_error")
            try:
                await self._log_connection_event("ERROR", {"error": str(data)})
            except Exception:
                metrics.incr_error("connection_log_error")

        @self.sio.on('gameStateUpdate')
        async def on_game_state(data):
            metrics.incr_message()
            await self._handle_game_state_update(data)

        @self.sio.on('standard/newTrade')
        async def on_new_trade(trade):
            metrics.incr_message()
            metrics.incr_trade()
            await self._handle_new_trade(trade)

        # Side bet related: only capture if the server actually emits these
        @self.sio.on('sideBet')
        async def on_side_bet(payload):
            metrics.incr_message()
            await self._handle_side_bet('sideBet', payload)

        @self.sio.on('standard/sideBetPlaced')
        async def on_side_bet_placed(payload):
            metrics.incr_message()
            await self._handle_side_bet('standard/sideBetPlaced', payload)

        @self.sio.on('standard/sideBetResult')
        async def on_side_bet_result(payload):
            metrics.incr_message()
            await self._handle_side_bet('standard/sideBetResult', payload)

        @self.sio.on('gameStatePlayerUpdate')
        async def on_game_state_player_update(payload):
            metrics.incr_message()
            await self._store_event("gameStatePlayerUpdate", payload)

        @self.sio.on('playerUpdate')
        async def on_player_update(payload):
            metrics.incr_message()
            await self._store_event("playerUpdate", payload)

        @self.sio.on('rugPool')
        async def on_rug_pool(payload):
            metrics.incr_message()
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
                await self.sio.wait()
                backoff = 1
            except Exception as e:
                logger.error(f"Socket.IO loop error: {e}")
                metrics.incr_error("socket_loop_error")
                try:
                    await self._log_connection_event("ERROR", {"error": str(e)})
                except Exception:
                    metrics.incr_error("connection_log_error")
                await asyncio.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)

    async def _log_connection_event(self, event_type: str, metadata: Dict[str, Any]):
        doc = {"_id": str(uuid.uuid4()), "socketId": self.socket_id, "eventType": event_type, "metadata": metadata, "timestampMs": int(datetime.now(tz=timezone.utc).timestamp() * 1000), "createdAt": now_utc()}
        await self.db.connection_events.insert_one(doc)

    # ---- core handlers ----
    async def _handle_game_state_update(self, data: Dict[str, Any]):
        self.last_event_at = now_utc()
        metrics.last_event_at = self.last_event_at
        phase = self._derive_phase(data)

        game_id = data.get("gameId")
        price = float(data.get("price") or 1.0)
        tick_count = int(data.get("tickCount") or 0)
        provably_fair = data.get("provablyFair") or {}
        version = provably_fair.get("version") or "v3"
        server_seed_hash = provably_fair.get("serverSeedHash")

        # Schema validation (warn mode)
        v_ok, v_err, v_key = (schema_registry.validate_inbound('gameStateUpdate', data) if schema_registry else (True, None, None))
        if v_key:
            metrics.incr_schema(v_key, bool(v_ok))

        # Broadcast minimal normalized frame to downstream
        await broadcaster.broadcast({
            "schema": "v1",
            "type": "game_state_update",
            "gameId": game_id,
            "tick": tick_count,
            "price": price,
            "phase": phase,
            "validation": {"ok": bool(v_ok), "schema": v_key},
            "ts": now_utc().isoformat(),
        })

        # Detect new active game
        if data.get("active") and (self.current_game_id != game_id):
            self.current_game_id = game_id
            metrics.add_game(game_id)
            self.game_stats[game_id] = {"peak": price, "ticks": tick_count, "last_price": price, "last_tick": tick_count, "god_candle_seen": False, "quality": {}, "last_seen_ts": time.time()}

            await self.db.meta.update_one({"key": "current_game_id"}, {"$set": {"key": "current_game_id", "value": game_id, "updatedAt": now_utc()}}, upsert=True)

            await self.db.games.update_one({"id": game_id}, {"$setOnInsert": {"id": game_id, "startTime": now_utc(), "createdAt": now_utc()}, "$set": {"phase": "ACTIVE", "version": version, "serverSeedHash": server_seed_hash, "lastSeenAt": now_utc()}}, upsert=True)

            if server_seed_hash:
                await self.db.prng_tracking.update_one({"gameId": game_id}, {"$setOnInsert": {"createdAt": now_utc()}, "$set": {"gameId": game_id, "serverSeedHash": server_seed_hash, "version": version, "status": "TRACKING", "updatedAt": now_utc()}}, upsert=True)

        # Data quality checks (lightweight, no scope creep)
        if game_id:
            stats = self.game_stats.get(game_id) or {"peak": 1.0, "ticks": 0, "last_price": price, "last_tick": tick_count, "god_candle_seen": False, "quality": {}}
            q = stats.get("quality", {})
            if tick_count <= stats.get("last_tick", -1):
                q["duplicateOrOutOfOrder"] = True
            if (tick_count - stats.get("last_tick", 0)) > 10:
                q["largeGap"] = True
            if price <= 0:
                q["priceNonPositive"] = True
            q["lastCheckedAt"] = now_utc()
            stats["quality"] = q

            # Update peak/ticks
            if price > stats["peak"]:
                stats["peak"] = price
            stats["ticks"] = tick_count

            # Persist quality flags and rolling stats
            await self.db.games.update_one({"id": game_id}, {"$set": {"peakMultiplier": stats["peak"], "totalTicks": stats["ticks"], "phase": "RUG" if phase == "RUG" else ("COOLDOWN" if phase == "COOLDOWN" else ("PRE_ROUND" if phase == "PRE_ROUND" else "ACTIVE" if phase == "ACTIVE" else "UNKNOWN")), "version": version, "serverSeedHash": server_seed_hash, "lastSeenAt": now_utc(), "quality": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in q.items()}}}, upsert=True)

            # ---- Tick persistence ----
            try:
                await self.db.game_ticks.update_one({"gameId": game_id, "tick": tick_count}, {"$setOnInsert": {"_id": str(uuid.uuid4()), "gameId": game_id, "tick": tick_count, "price": price, "createdAt": now_utc()}, "$set": {"updatedAt": now_utc()}}, upsert=True)
            except Exception as e:
                logger.error(f"game_ticks upsert error: {e}")
                metrics.incr_error("game_ticks_upsert")

            # ---- OHLC compaction per 5-tick index ----
            try:
                index = tick_count // 5
                start_tick = index * 5
                end_tick = start_tick + 4
                doc = await self.db.game_indices.find_one({"gameId": game_id, "index": index})
                if not doc:
                    await self.db.game_indices.update_one({"gameId": game_id, "index": index}, {"$setOnInsert": {"_id": str(uuid.uuid4()), "gameId": game_id, "index": index, "startTick": start_tick, "endTick": end_tick, "open": price, "high": price, "low": price, "close": price, "createdAt": now_utc()}, "$set": {"updatedAt": now_utc()}}, upsert=True)
                else:
                    high = max(doc.get("high", price), price)
                    low = min(doc.get("low", price), price)
                    await self.db.game_indices.update_one({"gameId": game_id, "index": index}, {"$set": {"high": high, "low": low, "close": price, "updatedAt": now_utc()}})
            except Exception as e:
                logger.error(f"game_indices upsert error: {e}")
                metrics.incr_error("game_indices_upsert")

            # ---- God Candle detection ----
            prev_price = None
            prices_arr = data.get("prices")
            if isinstance(prices_arr, list) and len(prices_arr) >= 2:
                prev_price = float(prices_arr[-2])
            else:
                prev_price = float(stats.get("last_price") or price)
            ratio = (price / prev_price) if prev_price and prev_price > 0 else 1.0
            existing = await self.db.god_candles.count_documents({"gameId": game_id, "tickIndex": int(tick_count)})
            is_god_candle = (ratio >= (GOD_CANDLE_MOVE - 1e-6)) and (existing == 0)
            if is_god_candle:
                try:
                    under_cap = prev_price <= 100 * STARTING_PRICE
                    gc_doc = {"_id": str(uuid.uuid4()), "gameId": game_id, "tickIndex": int(tick_count), "fromPrice": prev_price, "toPrice": price, "ratio": ratio, "version": version, "underCap": bool(under_cap), "createdAt": now_utc()}
                    await self.db.god_candles.insert_one(gc_doc)
                    await self.db.games.update_one({"id": game_id}, {"$set": {"hasGodCandle": True, "godCandleTick": int(tick_count), "godCandleFromPrice": prev_price, "godCandleToPrice": price, "updatedAt": now_utc()}})
                    await broadcaster.broadcast({"schema": "v1", "type": "god_candle", "gameId": game_id, "tick": tick_count, "fromPrice": prev_price, "toPrice": price, "ratio": ratio, "ts": now_utc().isoformat()})
                except Exception as e:
                    logger.error(f"God Candle persist error: {e}")
                    metrics.incr_error("god_candle_persist")

            stats["last_price"] = price
            stats["last_tick"] = tick_count
            stats["last_seen_ts"] = time.time()
            self.game_stats[game_id] = stats

        # Insert snapshot (observability)
        try:
            snap = {"_id": str(uuid.uuid4()), "gameId": game_id, "tickCount": tick_count, "active": data.get("active"), "rugged": data.get("rugged"), "price": price, "cooldownTimer": data.get("cooldownTimer"), "provablyFair": provably_fair, "phase": phase, "payload": data, "createdAt": now_utc()}
            if v_key:
                snap["validation"] = {"ok": bool(v_ok), "schema": v_key, "error": (v_err if not v_ok else None)}
            await self.db.game_state_snapshots.insert_one(snap)
        except Exception as e:
            logger.error(f"Snapshot insert error: {e}")
            metrics.incr_error("snapshot_insert")

        # Upsert live state singleton (HUD / API)
        try:
            lite = {"gameId": game_id, "active": data.get("active"), "rugged": data.get("rugged"), "price": price, "tickCount": tick_count, "cooldownTimer": data.get("cooldownTimer"), "provablyFair": provably_fair, "phase": phase, "updatedAt": now_utc()}
            await self.db.meta.update_one({"key": "live_state"}, {"$set": {"key": "live_state", **lite}}, upsert=True)
        except Exception as e:
            logger.error(f"Live state upsert error: {e}")
            metrics.incr_error("live_state_upsert")

        # Handle revealed server seeds for completed games & verify
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
                        await self.db.prng_tracking.update_one({"gameId": gid}, {"$set": {"serverSeed": srv_seed, "status": "COMPLETE", "updatedAt": now_utc()}}, upsert=True)
                        asyncio.create_task(run_prng_verification(gid))
                    await self.db.games.update_one({"id": gid}, {"$set": updates}, upsert=True)
        except Exception as e:
            logger.error(f"History upsert error: {e}")
            metrics.incr_error("history_upsert")

        # RUG end capture
        if data.get("rugged") and game_id:
            try:
                await self.db.games.update_one({"id": game_id}, {"$set": {"endTime": now_utc(), "phase": "RUG", "lastSeenAt": now_utc(), "rugTick": int(tick_count), "endPrice": float(price)}})
                await broadcaster.broadcast({"schema": "v1", "type": "rug", "gameId": game_id, "tick": tick_count, "endPrice": float(price), "ts": now_utc().isoformat()})
            except Exception as e:
                logger.error(f"RUG end update error: {e}")
                metrics.incr_error("rug_update")

    async def _handle_new_trade(self, trade: Dict[str, Any]):
        self.last_event_at = now_utc()
        # validation
        v_ok, v_err, v_key = (schema_registry.validate_inbound('standard/newTrade', trade) if schema_registry else (True, None, None))
        if v_key:
            metrics.incr_schema(v_key, bool(v_ok))
        try:
            doc = {"_id": str(uuid.uuid4()), "eventId": str(trade.get("id")), "gameId": trade.get("gameId"), "playerId": trade.get("playerId"), "type": trade.get("type"), "qty": trade.get("qty"), "tickIndex": trade.get("tickIndex"), "coin": trade.get("coin"), "amount": trade.get("amount"), "price": trade.get("price"), "createdAt": now_utc()}
            if v_key:
                doc["validation"] = {"ok": bool(v_ok), "schema": v_key, "error": (v_err if not v_ok else None)}
            # Idempotent insert on eventId when present
            if doc.get("eventId"):
                await self.db.trades.update_one(
                    {"eventId": doc["eventId"]},
                    {"$setOnInsert": doc},
                    upsert=True,
                )
            else:
                await self.db.trades.insert_one(doc)
            await broadcaster.broadcast({"schema": "v1", "type": "trade", "gameId": doc["gameId"], "playerId": doc["playerId"], "tradeType": doc["type"], "tickIndex": doc["tickIndex"], "amount": doc["amount"], "qty": doc["qty"], "price": doc.get("price"), "validation": {"ok": bool(v_ok), "schema": v_key}, "ts": now_utc().isoformat()})
        except Exception as e:
            logger.error(f"Trade insert error: {e}")
            metrics.incr_error("trade_insert")

    async def _handle_side_bet(self, event_type: str, payload: Dict[str, Any]):
        self.last_event_at = now_utc()
        # choose schema key based on event_type
        inbound_event = event_type
        v_ok, v_err, v_key = (schema_registry.validate_inbound(inbound_event, payload) if schema_registry else (True, None, None))
        if v_key:
            metrics.incr_schema(v_key, bool(v_ok))
        try:
            doc = {"_id": str(uuid.uuid4()), "event": event_type, "payload": payload, "createdAt": now_utc()}
            # Try to normalize common fields if present (no simulation)
            doc["gameId"] = payload.get("gameId")
            doc["playerId"] = payload.get("playerId") or payload.get("did")
            if "startTick" in payload:
                doc["startTick"] = int(payload["startTick"]) if payload["startTick"] is not None else None
            if "endTick" in payload:
                doc["endTick"] = int(payload["endTick"]) if payload["endTick"] is not None else None
            for k in ["betAmount", "targetSeconds", "payoutRatio", "won", "pnl", "xPayout"]:
                if k in payload:
                    doc[k] = payload[k]
            if v_key:
                doc["validation"] = {"ok": bool(v_ok), "schema": v_key, "error": (v_err if not v_ok else None)}
            await self.db.side_bets.insert_one(doc)
            await broadcaster.broadcast({"schema": "v1", "type": "side_bet", "event": event_type, "gameId": doc.get("gameId"), "playerId": doc.get("playerId"), "validation": {"ok": bool(v_ok), "schema": v_key}, "ts": now_utc().isoformat()})
        except Exception as e:
            logger.error(f"Side bet store error: {e}")
            metrics.incr_error("side_bet_insert")

    async def _store_event(self, event_type: str, payload: Dict[str, Any]):
        self.last_event_at = now_utc()
        v_ok, v_err, v_key = (schema_registry.validate_inbound(event_type, payload) if schema_registry else (True, None, None))
        if v_key:
            metrics.incr_schema(v_key, bool(v_ok))
        try:
            doc = {"_id": str(uuid.uuid4()), "type": event_type, "payload": payload, "createdAt": now_utc()}
            if v_key:
                doc["validation"] = {"ok": bool(v_ok), "schema": v_key, "error": (v_err if not v_ok else None)}
            await self.db.events.insert_one(doc)
        except Exception as e:
            logger.error(f"Event store error: {e}")
            metrics.incr_error("event_insert")

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
# API Routes (REST)
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

@api_router.get("/metrics")
async def metrics_endpoint():
    mps_1m = metrics.msgs_per_sec_window(60)
    mps_5m = metrics.msgs_per_sec_window(300)
    connected_clients = len(broadcaster.connections)
    return {
        "serviceUptimeSec": int(time.time() - metrics.start_time),
        "currentSocketConnected": bool(auth_svc and auth_svc.connected),
        "socketId": (auth_svc.socket_id if auth_svc else None),
        "lastEventAt": (metrics.last_event_at.isoformat() if metrics.last_event_at else None),
        "lastErrorAt": (metrics.last_error_at.isoformat() if metrics.last_error_at else None),
        "totalMessagesProcessed": metrics.total_messages,
        "totalTrades": metrics.total_trades,
        "totalGamesTracked": len(metrics.total_games_seen),
        "messagesPerSecond1m": round(mps_1m, 3),
        "messagesPerSecond5m": round(mps_5m, 3),
        "wsSubscribers": connected_clients,
        "wsSlowClientDrops": metrics.ws_slow_client_drops,
        "dbPingMs": metrics.last_db_ping_ms,
        "errorCounters": metrics.error_counts,
        "schemaValidation": metrics.schema_validation,
    }

@api_router.get("/connection", response_model=ConnectionState)
async def connection():
    if auth_svc is None:
        return ConnectionState(connected=False)
    since_ms = None
    if auth_svc.connected_at_ms:
        since_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000) - auth_svc.connected_at_ms
    return ConnectionState(connected=auth_svc.connected, socket_id=auth_svc.socket_id, last_event_at=auth_svc.last_event_at, since_connected_ms=since_ms)

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

@api_router.get("/god-candles")
async def god_candles(gameId: Optional[str] = Query(default=None), limit: int = 50):
    limit = max(1, min(limit, 200))
    q: Dict[str, Any] = {}
    if gameId:
        q["gameId"] = gameId
    rows = await db.god_candles.find(q).sort("createdAt", -1).to_list(limit)
    for r in rows:
        r["id"] = r.pop("_id", None)
        if isinstance(r.get("createdAt"), datetime):
            r["createdAt"] = r["createdAt"].isoformat()
    return {"items": rows}

@api_router.get("/ohlc")
async def ohlc(gameId: str = Query(...), window: int = Query(5), limit: int = Query(200)):
    if window != 5:
        raise HTTPException(status_code=400, detail="Only 5-tick window supported currently")
    limit = max(1, min(limit, 1000))
    rows = await db.game_indices.find({"gameId": gameId}).sort("index", -1).limit(limit).to_list(limit)
    for r in rows:
        r["id"] = r.pop("_id", None)
        for dkey in ["createdAt", "updatedAt"]:
            if isinstance(r.get(dkey), datetime):
                r[dkey] = r[dkey].isoformat()
    return {"items": rows}

@api_router.get("/games")
async def games(limit: int = 50):
    limit = max(1, min(limit, 200))
    rows = await db.games.find().sort("lastSeenAt", -1).to_list(limit)
    for r in rows:
        r.pop("_id", None)
        for dkey in ["lastSeenAt", "startTime", "endTime", "createdAt", "updatedAt"]:
            if isinstance(r.get(dkey), datetime):
                r[dkey] = r[dkey].isoformat()
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


@api_router.get("/readiness")
async def readiness():
    db_ok = False
    ping_ms = None
    t0 = time.time()
    try:
        await db.command({"ping": 1})
        db_ok = True
        ping_ms = int((time.time() - t0) * 1000)
        metrics.last_db_ping_ms = ping_ms
    except Exception as e:
        logger.warning(f"Mongo ping failed: {e}")
        metrics.incr_error("db_ping_failed")
    upstream_ok = bool(auth_svc and auth_svc.connected)
    return {"dbOk": db_ok, "dbPingMs": ping_ms, "upstreamConnected": upstream_ok, "time": now_utc().isoformat()}

@api_router.get("/games/{game_id}/quality")
async def game_quality(game_id: str):
    g = await db.games.find_one({"id": game_id}, {"quality": 1, "_id": 0})
    return g.get("quality") if g else {}

@api_router.get("/quality")
async def quality_list(limit: int = 50):
    limit = max(1, min(limit, 200))
    rows = await db.games.find({"quality": {"$exists": True}}).sort("lastSeenAt", -1).limit(limit).to_list(limit)
    out = []
    for r in rows:
        out.append({"id": r.get("id"), "quality": r.get("quality")})
    return {"items": out}

    # Periodic prune of in-memory game_stats to prevent unbounded growth
    try:
        if auth_svc and isinstance(auth_svc.game_stats, dict):
            now_ts = time.time()
            # Remove games not updated in > 24h or keep only most recent 200 by last_tick
            if len(auth_svc.game_stats) > 250:
                # sort by last_tick desc
                keep = sorted(auth_svc.game_stats.items(), key=lambda kv: (kv[1].get("last_tick", 0)), reverse=True)[:200]
                auth_svc.game_stats = dict(keep)
            # time-based prune using no explicit timestamps; rely on size cap above
    except Exception as e:
        logger.warning(f"game_stats prune warn: {e}")

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
    # run_prng_verification defined earlier
    result = await run_prng_verification(game_id)
    return result

@api_router.get("/schemas")
async def list_schemas():
    if not schema_registry:
        return {"items": []}
    return schema_registry.describe()

########################################################
# WebSocket route for downstream consumers
########################################################

@app.websocket("/api/ws/stream")
async def ws_stream(ws: WebSocket):
    await broadcaster.register(ws)
    try:
        # Send a hello + minimal status
        await ws.send_json({"type": "hello", "time": now_utc().isoformat()})
        while True:
            # Keep alive: we don't expect incoming messages, but read pings if any
            await asyncio.sleep(30)
            try:
                await ws.send_json({"type": "heartbeat", "time": now_utc().isoformat()})
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(ws)

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
# Lifespan hooks & backfill
########################################################

async def backfill_god_candle_flags(limit: int = 2000):
    try:
        cursor = db.god_candles.find({}, {"gameId": 1, "tickIndex": 1, "fromPrice": 1, "toPrice": 1}).sort("createdAt", -1).limit(limit)
        async for gc in cursor:
            gid = gc.get("gameId")
            if not gid:
                continue
            await db.games.update_one({"id": gid}, {"$set": {"hasGodCandle": True, "godCandleTick": int(gc.get("tickIndex", 0)), "godCandleFromPrice": float(gc.get("fromPrice", 0)), "godCandleToPrice": float(gc.get("toPrice", 0)), "updatedAt": now_utc()}}, upsert=True)
    except Exception as e:
        logger.warning(f"God Candle backfill warning: {e}")

@app.on_event("startup")
async def startup_event():
    global auth_svc, schema_registry
    await ensure_indexes()
    # load schemas
    try:
        schema_registry = SchemaRegistry(SCHEMA_DIR)
        logger.info("SchemaRegistry loaded")
    except Exception as e:
        logger.warning(f"SchemaRegistry load failed: {e}")
    asyncio.create_task(backfill_god_candle_flags())
    auth_svc = RugsSocketService(db)
    auth_svc.start()
    logger.info("Rugs Socket Service started")

@app.on_event("shutdown")
async def shutdown_db_client():
    try:
        if auth_svc:
            await auth_svc.stop()
    finally:
        client.close()
