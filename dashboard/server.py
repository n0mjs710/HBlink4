"""
HBlink4 Dashboard - Separate Process
FastAPI + Uvicorn + WebSockets for re        self.stats['total_streams_today'] = 0
        self.stats['total_duration_today'] = 0
        self.stats['total_calls_today'] = 0ime monitoring

Updates every 10 superframes (60 packets = 1 second) for smooth real-time feel
"""
import asyncio
import json
from d        # Send to all WebSocket clients
        await self.send_to_clients(event)
    
    async def send_to_clients(self, event: dict):
        """Send event to all connected WebSocket clients"""me import datetime, date
from collections import deque
from typing import Dict, List, Set, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="HBlink4 Dashboard", version="1.0.0")

# Load dashboard configuration
def load_config() -> dict:
    """Load dashboard configuration from config.json"""
    config_path = Path(__file__).parent / "config.json"
    default_config = {
        "server_name": "HBlink4 Server",
        "server_description": "Amateur Radio DMR Network",
        "dashboard_title": "HBlink4 Dashboard",
        "refresh_interval": 1000,
        "max_events": 50
    }
    
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
                # Merge with defaults (in case new keys are added)
                return {**default_config, **config}
        except Exception as e:
            logger.warning(f"Failed to load config.json: {e}, using defaults")
            return default_config
    else:
        # Create default config file
        try:
            with open(config_path, 'w') as f:
                json.dump(default_config, f, indent=4)
            logger.info(f"Created default config at {config_path}")
        except Exception as e:
            logger.warning(f"Failed to create config.json: {e}")
        return default_config

dashboard_config = load_config()

# In-memory state (could be Redis/database for persistence)
class DashboardState:
    """Global dashboard state"""
    
    def __init__(self):
        self.repeaters: Dict[int, dict] = {}
        self.streams: Dict[str, dict] = {}  # key: f"{repeater_id}.{slot}"
        self.events: deque = deque(maxlen=500)  # Ring buffer of recent events
        self.last_heard: List[dict] = []  # Last heard users
        self.last_heard_stats: dict = {}  # User cache statistics
        self.websocket_clients: Set[WebSocket] = set()
        self.stats = {
            'total_streams_today': 0,
            'total_duration_today': 0.0,  # Total duration in seconds
            'packets_forwarded': 0,  # Total packets forwarded
            'packets_received': 0,  # Total packets received
            'start_time': datetime.now().isoformat(),
            'last_reset_date': date.today().isoformat()  # Track when stats were last reset
        }
    
    def reset_daily_stats(self):
        """Reset daily statistics at midnight"""
        self.stats['total_streams_today'] = 0
        self.stats['total_duration_today'] = 0.0
        self.stats['packets_forwarded'] = 0
        self.stats['packets_received'] = 0
        self.stats['last_reset_date'] = date.today().isoformat()
        logger.info(f"📊 Daily stats reset at midnight (server time)")

state = DashboardState()


class UDPProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler for receiving events from hblink4"""
    
    def __init__(self, callback):
        self.callback = callback
    
    def datagram_received(self, data, addr):
        """Called when UDP datagram received"""
        asyncio.create_task(self.callback(data))


class EventReceiver:
    """Receives UDP datagrams from hblink4 and processes events"""
    
    def __init__(self, host='127.0.0.1', port=8765):
        self.host = host
        self.port = port
    
    async def start(self):
        """Start receiving UDP datagrams from hblink4"""
        loop = asyncio.get_event_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: UDPProtocol(self.process_event),
            local_addr=(self.host, self.port)
        )
        logger.info(f"📡 Listening for HBlink4 events on {self.host}:{self.port}")
    
    async def process_event(self, data: bytes):
        """Process incoming event from hblink4"""
        try:
            event = json.loads(data.decode('utf-8'))
            await self.handle_event(event)
        except Exception as e:
            logger.error(f"Error processing event: {e}")
    
    async def handle_event(self, event: dict):
        """Update state and broadcast to WebSocket clients"""
        event_type = event['type']
        data = event['data']
        
        # Update internal state based on event type
        if event_type == 'repeater_connected':
            state.repeaters[data['radio_id']] = {
                **data,
                'connected_at': event['timestamp'],
                'last_activity': event['timestamp'],
                'last_ping': data.get('last_ping', event['timestamp']),
                'missed_pings': data.get('missed_pings', 0),
                'status': 'connected'
            }
            logger.info(f"Repeater connected: {data['radio_id']} ({data.get('callsign', 'UNKNOWN')})")
        
        elif event_type == 'repeater_keepalive':
            if data['radio_id'] in state.repeaters:
                state.repeaters[data['radio_id']]['last_ping'] = data.get('last_ping', event['timestamp'])
                state.repeaters[data['radio_id']]['missed_pings'] = data.get('missed_pings', 0)
                state.repeaters[data['radio_id']]['last_activity'] = event['timestamp']
        
        elif event_type == 'repeater_disconnected':
            if data['radio_id'] in state.repeaters:
                state.repeaters[data['radio_id']]['status'] = 'disconnected'
                logger.info(f"Repeater disconnected: {data['radio_id']}")
        
        elif event_type == 'stream_start':
            key = f"{data['repeater_id']}.{data['slot']}"
            state.streams[key] = {
                **data,
                'start_time': event['timestamp'],
                'packets': 0,
                'duration': 0,
                'status': 'active'
            }
            state.stats['total_streams_today'] += 1
            
            # Update repeater last activity
            if data['repeater_id'] in state.repeaters:
                state.repeaters[data['repeater_id']]['last_activity'] = event['timestamp']
        
        elif event_type == 'stream_update':
            key = f"{data['repeater_id']}.{data['slot']}"
            if key in state.streams:
                state.streams[key]['packets'] = data['packets']
                state.streams[key]['duration'] = data['duration']
        
        elif event_type == 'stream_end':
            key = f"{data['repeater_id']}.{data['slot']}"
            if key in state.streams:
                # Stream ended and entering hang time (combined event)
                state.streams[key]['status'] = 'hang_time'
                state.streams[key]['packets'] = data['packets']
                state.streams[key]['duration'] = data['duration']
                state.streams[key]['end_reason'] = data.get('end_reason', 'unknown')
                state.streams[key]['hang_time'] = data.get('hang_time', 0)
                # Accumulate total duration when stream ends
                state.stats['total_duration_today'] += data['duration']
        
        elif event_type == 'hang_time_expired':
            # Hang time has expired, clear the slot
            key = f"{data['repeater_id']}.{data['slot']}"
            if key in state.streams:
                del state.streams[key]
                logger.debug(f"Hang time expired for {key}")
        
        elif event_type == 'last_heard_update':
            # Update last heard users (don't add to events log - not an on-air event)
            state.last_heard = data.get('users', [])
            state.last_heard_stats = data.get('stats', {})
            logger.debug(f"Last heard updated: {len(state.last_heard)} users")
            # Send to WebSocket clients but skip adding to events log
            await self.send_to_clients(event)
            return
        
        elif event_type == 'forwarding_stats':
            # Update forwarding statistics (don't add to events log)
            state.stats['active_calls'] = data.get('active_calls', 0)
            state.stats['total_calls_today'] = data.get('total_calls_today', 0)
            logger.debug(f"Forwarding stats updated: Active={data.get('active_calls', 0)}, Total Today={data.get('total_calls_today', 0)}")
            # Send to WebSocket clients but skip adding to events log
            await self.send_to_clients(event)
            return
        
        # Add to event log (only for user-facing on-air activity events)
        # Skip system events like repeater_connected, repeater_disconnected, hang_time_expired, repeater_keepalive
        if event_type in ['stream_start', 'stream_end']:
            state.events.append(event)
        
        # Send to all WebSocket clients
        await self.send_to_clients(event)
    
    async def send_to_clients(self, event: dict):
        """Send event to all connected WebSocket clients"""
        if not state.websocket_clients:
            return
        
        message = json.dumps(event)
        disconnected = set()
        
        for client in state.websocket_clients:
            try:
                await client.send_text(message)
            except:
                disconnected.add(client)
        
        # Remove disconnected clients
        state.websocket_clients -= disconnected


# REST API endpoints
@app.get("/api/config")
async def get_config():
    """Get dashboard configuration"""
    return dashboard_config


@app.get("/api/repeaters")
async def get_repeaters():
    """Get all connected repeaters"""
    return {"repeaters": list(state.repeaters.values())}


@app.get("/api/streams")
async def get_streams():
    """Get all active streams"""
    return {"streams": list(state.streams.values())}


@app.get("/api/events")
async def get_events(limit: int = 100):
    """Get recent events"""
    return {"events": list(state.events)[-limit:]}


@app.get("/api/stats")
async def get_stats():
    """Get system statistics"""
    return {
        "stats": state.stats,
        "repeaters_connected": len([r for r in state.repeaters.values() if r.get('status') == 'connected']),
        "active_streams": len([s for s in state.streams.values() if s.get('status') == 'active'])
    }


# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket connection for real-time updates"""
    await websocket.accept()
    state.websocket_clients.add(websocket)
    logger.debug(f"WebSocket client connected (total: {len(state.websocket_clients)})")
    
    # Send initial state
    await websocket.send_json({
        'type': 'initial_state',
        'data': {
            'repeaters': list(state.repeaters.values()),
            'streams': list(state.streams.values()),
            'events': list(state.events)[-50:],
            'stats': state.stats,
            'last_heard': state.last_heard
        }
    })
    
    try:
        while True:
            # Keep connection alive (client can send ping)
            data = await websocket.receive_text()
            if data == 'ping':
                await websocket.send_text('pong')
    except WebSocketDisconnect:
        state.websocket_clients.discard(websocket)
        logger.debug(f"WebSocket client disconnected (remaining: {len(state.websocket_clients)})")


# Serve frontend
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve dashboard HTML"""
    html_path = Path(__file__).parent / 'static' / 'dashboard.html'
    if not html_path.exists():
        return HTMLResponse("<h1>Dashboard HTML not found</h1><p>Please create dashboard/static/dashboard.html</p>", status_code=404)
    with open(html_path) as f:
        return HTMLResponse(f.read())


# Mount static files
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.on_event("startup")
async def startup_event():
    """Start event receiver on startup"""
    receiver = EventReceiver()
    asyncio.create_task(receiver.start())
    asyncio.create_task(midnight_reset_task())
    logger.info("🚀 HBlink4 Dashboard started!")
    logger.info("📊 Access dashboard at http://localhost:8080")


async def midnight_reset_task():
    """Background task to reset daily stats at midnight"""
    while True:
        # Check if date has changed
        current_date = date.today().isoformat()
        if current_date != state.stats.get('last_reset_date'):
            state.reset_daily_stats()
            # Send stats update to all WebSocket clients
            await send_stats_update()
        
        # Check every 60 seconds
        await asyncio.sleep(60)


async def send_stats_update():
    """Send stats update to all WebSocket clients"""
    if not state.websocket_clients:
        return
    
    message = json.dumps({
        'type': 'stats_reset',
        'timestamp': datetime.now().timestamp(),
        'data': state.stats
    })
    
    disconnected = set()
    for client in state.websocket_clients:
        try:
            await client.send_text(message)
        except:
            disconnected.add(client)
    
    # Remove disconnected clients
    state.websocket_clients -= disconnected


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "dashboard.server:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        access_log=False  # Disable access logging (reduces log clutter)
    )
