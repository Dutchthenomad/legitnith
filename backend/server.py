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
# Utility helpers
########################################################

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

async def ensure_indexes():
    # Create indexes used by queries
    await db.game_state_snapshots.create_index([("gameId", 1), ("tickCount", -1)])
    await db.game_state_snapshots.create_index([("createdAt", -1)])
    await db.trades.create_index([("gameId", 1), ("tickIndex", 1)])
    await db.games.create_index([("id", 1)], unique=True)
    await db.events.create_index([("type", 1), ("createdAt", -1)])

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
        # Derive phase
        phase = self._derive_phase(data)
        # Upsert game meta
        try:
            game_id = data.get("gameId")
            provably_fair = data.get("provablyFair") or {}
            update = {
                "id": game_id,
                "lastSeenAt": now_utc(),
                "provablyFair": provably_fair,
            }
            await self.db.games.update_one({"id": game_id}, {"$set": update}, upsert=True)
        except Exception as e:
            logger.error(f"Game upsert error: {e}")

        # Insert snapshot
        try:
            snap = {
                "_id": str(uuid.uuid4()),
                "gameId": data.get("gameId"),
                "tickCount": data.get("tickCount"),
                "active": data.get("active"),
                "rugged": data.get("rugged"),
                "price": data.get("price"),
                "cooldownTimer": data.get("cooldownTimer"),
                "provablyFair": data.get("provablyFair"),
                "phase": phase,
                "payload": data,
                "createdAt": now_utc(),
            }
            await self.db.game_state_snapshots.insert_one(snap)
        except Exception as e:
            logger.error(f"Snapshot insert error: {e}")

        # Upsert live state singleton (for quick read)
        try:
            lite = {
                "gameId": data.get("gameId"),
                "active": data.get("active"),
                "rugged": data.get("rugged"),
                "price": data.get("price"),
                "tickCount": data.get("tickCount"),
                "cooldownTimer": data.get("cooldownTimer"),
                "provablyFair": data.get("provablyFair"),
                "phase": phase,
                "updatedAt": now_utc(),
            }
            await self.db.meta.update_one({"key": "live_state"}, {"$set": {"key": "live_state", **lite}}, upsert=True)
        except Exception as e:
            logger.error(f"Live state upsert error: {e}")

        # If history supplied during cooldown, try to capture revealed server seeds
        try:
            history = data.get("gameHistory")
            if isinstance(history, list):
                for g in history:
                    gid = g.get("id") or g.get("gameId")
                    if not gid:
                        continue
                    await self.db.games.update_one({"id": gid}, {"$set": {"id": gid, "history": g, "lastSeenAt": now_utc()}}, upsert=True)
        except Exception as e:
            logger.error(f"History upsert error: {e}")

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
            return "RUG_EVENT"
        if active and not rugged:
            return "ACTIVE_GAMEPLAY"
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
    # Rows may include _id; normalize
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
    return {"items": rows}

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
import contextlib

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