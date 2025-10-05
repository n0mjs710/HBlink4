"""
HBlink4 Dashboard - Separate Process
FastAPI + Uvicorn + WebSockets for real-time monitoring

Updates every 10 superframes (60 packets = 1 second) for smooth real-time feel
"""
import asyncio
import json
import csv
import socket
import os
from datetime import datetime, date
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

# Load user database from CSV
def load_user_database() -> Dict[int, str]:
    """
    Load user.csv file for callsign lookups.
    Returns dict mapping radio_id -> callsign (memory efficient).
    """
    user_db = {}
    user_csv_path = Path(__file__).parent.parent / "user.csv"
    
    if not user_csv_path.exists():
        logger.warning(f"user.csv not found at {user_csv_path}, callsign lookups disabled")
        return user_db
    
    try:
        with open(user_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    radio_id = int(row['RADIO_ID'])
                    callsign = row['CALLSIGN'].strip()
                    if callsign:  # Only store non-empty callsigns
                        user_db[radio_id] = callsign
                except (ValueError, KeyError):
                    continue  # Skip malformed rows
        
        logger.info(f"Loaded {len(user_db)} users from user.csv (~{len(user_db) * 14 // 1024} KB)")
        return user_db
    except Exception as e:
        logger.error(f"Failed to load user.csv: {e}")
        return user_db

user_database = load_user_database()

# Load dashboard configuration
def load_config() -> dict:
    """Load dashboard configuration from config.json"""
    config_path = Path(__file__).parent / "config.json"
    default_config = {
        "server_name": "HBlink4 Server",
        "server_description": "Amateur Radio DMR Network",
        "dashboard_title": "HBlink4 Dashboard",
        "refresh_interval": 1000,
        "max_events": 50,
        "event_receiver": {
            "transport": "unix",
            "host": "127.0.0.1",
            "port": 8765,
            "unix_socket": "/tmp/hblink4.sock",
            "ipv6": False,
            "buffer_size": 65536
        }
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
            'active_calls': 0,  # Currently active forwarded calls
            'total_calls_today': 0,  # Total calls forwarded today
            'start_time': datetime.now().isoformat(),
            'last_reset_date': date.today().isoformat()  # Track when stats were last reset
        }
    
    def reset_daily_stats(self):
        """Reset daily statistics at midnight"""
        self.stats['total_streams_today'] = 0
        self.stats['total_duration_today'] = 0.0
        self.stats['total_calls_today'] = 0
        self.stats['last_reset_date'] = date.today().isoformat()
        logger.info(f"📊 Daily stats reset at midnight (server time)")

state = DashboardState()


class TCPProtocol(asyncio.Protocol):
    """TCP protocol handler for receiving events from hblink4"""
    
    def __init__(self, callback):
        self.callback = callback
        self.buffer = b''
        self.transport = None
    
    def connection_made(self, transport):
        """Called when TCP connection established"""
        peername = transport.get_extra_info('peername')
        logger.info(f"✅ HBlink4 connected via TCP from {peername}")
        self.transport = transport
    
    def data_received(self, data):
        """Called when TCP data received (handles framing)"""
        self.buffer += data
        
        # Process all complete frames in buffer
        while len(self.buffer) >= 4:
            # Read length prefix (4 bytes, big-endian)
            length = int.from_bytes(self.buffer[:4], byteorder='big')
            
            # Check if we have complete frame
            if len(self.buffer) < 4 + length:
                break  # Wait for more data
            
            # Extract frame
            frame = self.buffer[4:4+length]
            self.buffer = self.buffer[4+length:]
            
            # Process event
            asyncio.create_task(self.callback(frame))
    
    def connection_lost(self, exc):
        """Called when TCP connection lost"""
        if exc:
            logger.warning(f"⚠️ HBlink4 TCP connection lost: {exc}")
        else:
            logger.info("HBlink4 TCP connection closed")


class UnixProtocol(asyncio.Protocol):
    """Unix socket protocol handler for receiving events from hblink4"""
    
    def __init__(self, callback):
        self.callback = callback
        self.buffer = b''
        self.transport = None
    
    def connection_made(self, transport):
        """Called when Unix socket connection established"""
        logger.info(f"✅ HBlink4 connected via Unix socket")
        self.transport = transport
    
    def data_received(self, data):
        """Called when data received (handles framing)"""
        self.buffer += data
        
        # Process all complete frames in buffer
        while len(self.buffer) >= 4:
            # Read length prefix (4 bytes, big-endian)
            length = int.from_bytes(self.buffer[:4], byteorder='big')
            
            # Check if we have complete frame
            if len(self.buffer) < 4 + length:
                break  # Wait for more data
            
            # Extract frame
            frame = self.buffer[4:4+length]
            self.buffer = self.buffer[4+length:]
            
            # Process event
            asyncio.create_task(self.callback(frame))
    
    def connection_lost(self, exc):
        """Called when Unix socket connection lost"""
        if exc:
            logger.warning(f"⚠️ HBlink4 Unix socket connection lost: {exc}")
        else:
            logger.info("HBlink4 Unix socket connection closed")


class EventReceiver:
    """Receives events from hblink4 via TCP or Unix socket"""
    
    def __init__(self, transport='unix', host='127.0.0.1', port=8765, 
                 unix_socket='/tmp/hblink4.sock', ipv6=False):
        """
        Initialize event receiver with transport abstraction
        
        Args:
            transport: 'tcp' or 'unix'
            host: Listen address (for TCP)
            port: Listen port (for TCP)
            unix_socket: Unix socket path (for Unix transport)
            ipv6: Use IPv6 (for TCP)
        """
        self.transport = transport.lower()
        self.host = host
        self.port = port
        self.unix_socket = unix_socket
        self.ipv6 = ipv6
        self.server = None
    
    async def start(self):
        """Start receiving events from hblink4"""
        loop = asyncio.get_event_loop()
        
        if self.transport == 'tcp':
            await self._start_tcp(loop)
        elif self.transport == 'unix':
            await self._start_unix(loop)
        else:
            logger.error(f"Unknown transport: {self.transport} (valid options: 'tcp', 'unix')")
            raise ValueError(f"Unknown transport: {self.transport}")
    
    async def _start_tcp(self, loop):
        """Start TCP server"""
        family = socket.AF_INET6 if self.ipv6 else socket.AF_INET
        self.server = await loop.create_server(
            lambda: TCPProtocol(self.process_event),
            self.host, self.port,
            family=family
        )
        logger.info(f"📡 Listening for HBlink4 events via TCP on {self.host}:{self.port}")
    
    async def _start_unix(self, loop):
        """Start Unix socket server"""
        # Remove existing socket file if it exists
        if os.path.exists(self.unix_socket):
            try:
                os.unlink(self.unix_socket)
                logger.info(f"Removed existing socket file: {self.unix_socket}")
            except Exception as e:
                logger.warning(f"Failed to remove existing socket: {e}")
        
        self.server = await loop.create_unix_server(
            lambda: UnixProtocol(self.process_event),
            self.unix_socket
        )
        
        # Set socket permissions (readable/writable by owner and group)
        try:
            os.chmod(self.unix_socket, 0o660)
        except Exception as e:
            logger.warning(f"Failed to set socket permissions: {e}")
        
        logger.info(f"📡 Listening for HBlink4 events via Unix socket at {self.unix_socket}")
    
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
            state.repeaters[data['repeater_id']] = {
                **data,
                'connected_at': event['timestamp'],
                'last_activity': event['timestamp'],
                'last_ping': data.get('last_ping', event['timestamp']),
                'missed_pings': data.get('missed_pings', 0),
                'status': 'connected'
            }
            logger.info(f"Repeater connected: {data['repeater_id']} ({data.get('callsign', 'UNKNOWN')})")
        
        elif event_type == 'repeater_keepalive':
            if data['repeater_id'] in state.repeaters:
                state.repeaters[data['repeater_id']]['last_ping'] = data.get('last_ping', event['timestamp'])
                state.repeaters[data['repeater_id']]['missed_pings'] = data.get('missed_pings', 0)
                state.repeaters[data['repeater_id']]['last_activity'] = event['timestamp']
        
        elif event_type == 'repeater_disconnected':
            if data['repeater_id'] in state.repeaters:
                state.repeaters[data['repeater_id']]['status'] = 'disconnected'
                logger.info(f"Repeater disconnected: {data['repeater_id']}")
        
        elif event_type == 'repeater_options_updated':
            # RPTO received - update TG lists in real-time
            if data['repeater_id'] in state.repeaters:
                state.repeaters[data['repeater_id']]['slot1_talkgroups'] = data.get('slot1_talkgroups', [])
                state.repeaters[data['repeater_id']]['slot2_talkgroups'] = data.get('slot2_talkgroups', [])
                state.repeaters[data['repeater_id']]['rpto_received'] = data.get('rpto_received', False)
                logger.info(f"Repeater options updated via RPTO: {data['repeater_id']}")
        
        elif event_type == 'stream_start':
            key = f"{data['repeater_id']}.{data['slot']}"
            
            # Look up callsign from user database
            src_id = data.get('src_id')
            callsign = user_database.get(src_id, '') if src_id else ''
            
            state.streams[key] = {
                **data,
                'callsign': callsign,  # Add callsign to stream data
                'start_time': event['timestamp'],
                'packets': 0,
                'duration': 0,
                'status': 'active'
            }
            state.stats['total_streams_today'] += 1
            
            # Add/update user in last_heard immediately with "active" status
            if src_id:
                # Find existing entry or create new one
                existing_idx = next((i for i, u in enumerate(state.last_heard) if u['radio_id'] == src_id), None)
                user_entry = {
                    'radio_id': src_id,
                    'callsign': callsign,
                    'repeater_id': data['repeater_id'],
                    'slot': data['slot'],
                    'talkgroup': data.get('dst_id', 0),
                    'last_heard': event['timestamp'],
                    'active': True  # Mark as currently active
                }
                
                if existing_idx is not None:
                    state.last_heard[existing_idx] = user_entry
                else:
                    state.last_heard.insert(0, user_entry)  # Add to front
                
                # Keep only most recent 10 entries
                state.last_heard = state.last_heard[:10]
            
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
                stream = state.streams[key]
                stream['status'] = 'hang_time'
                stream['packets'] = data['packets']
                stream['duration'] = data['duration']
                stream['end_reason'] = data.get('end_reason', 'unknown')
                stream['hang_time'] = data.get('hang_time', 0)
                # Accumulate total duration when stream ends
                state.stats['total_duration_today'] += data['duration']
                
                # Update last_heard entry to mark as no longer active
                src_id = stream.get('src_id')
                if src_id:
                    user_entry = next((u for u in state.last_heard if u['radio_id'] == src_id), None)
                    if user_entry:
                        user_entry['active'] = False
                        user_entry['last_heard'] = event['timestamp']
        
        elif event_type == 'hang_time_expired':
            # Hang time has expired, clear the slot
            key = f"{data['repeater_id']}.{data['slot']}"
            if key in state.streams:
                del state.streams[key]
                logger.debug(f"Hang time expired for {key}")
        
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
        
        # For stream events, include updated last_heard list in the event
        if event_type in ['stream_start', 'stream_end', 'hang_time_expired']:
            event['last_heard'] = state.last_heard
        
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
    receiver_config = dashboard_config.get('event_receiver', {})
    receiver = EventReceiver(
        transport=receiver_config.get('transport', 'unix'),
        host=receiver_config.get('host', '127.0.0.1'),
        port=receiver_config.get('port', 8765),
        unix_socket=receiver_config.get('unix_socket', '/tmp/hblink4.sock'),
        ipv6=receiver_config.get('ipv6', False)
    )
    asyncio.create_task(receiver.start())
    asyncio.create_task(midnight_reset_task())
    logger.info("🚀 HBlink4 Dashboard started!")
    logger.info(f"📡 Event transport: {receiver_config.get('transport', 'unix').upper()}")
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
