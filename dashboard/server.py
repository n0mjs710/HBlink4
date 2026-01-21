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
import ipaddress
import signal
import atexit
from datetime import datetime, date, timedelta
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

app = FastAPI(title="HBlink4 Dashboard", version="1.1.0")

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
    """Maintains state for the dashboard"""
    def __init__(self):
        self.repeaters: Dict[int, dict] = {}
        self.repeater_details: Dict[int, dict] = {}  # Detailed info (sent once per connection)
        self.outbounds: Dict[str, dict] = {}  # Outbound connections (key: connection_name)
        self.streams: Dict[str, dict] = {}  # key: f"{repeater_id}.{slot}"
        self.events: deque = deque(maxlen=500)  # Ring buffer of recent events
        self.last_heard: List[dict] = []  # Last heard users
        self.last_heard_stats: dict = {}  # User cache statistics
        self.websocket_clients: Set[WebSocket] = set()
        self.hblink_connected: bool = False  # Track HBlink4 connection status
        self.stats = {
            'total_calls_today': 0,      # Total RX calls (streams) received today
            'total_duration_today': 0.0,  # Total duration of RX streams only (seconds)
            'retransmitted_calls': 0,    # Total calls retransmitted today (may be higher than received)
            'start_time': datetime.now().isoformat(),
            'last_reset_date': date.today().isoformat()  # Track when stats were last reset
        }
        
        # Persistence file paths
        self._data_dir = Path(__file__).parent / "data"
        self._stats_file = self._data_dir / "stats.json"
        self._last_heard_file = self._data_dir / "last_heard.json"
        self._persistence_disabled = False  # Will be set to True if write permissions fail
        
        # Load persisted data
        self._load_persisted_data()
    
    def reset_daily_stats(self):
        """Reset daily statistics at midnight"""
        self.stats['total_calls_today'] = 0
        self.stats['total_duration_today'] = 0.0
        self.stats['retransmitted_calls'] = 0
        self.stats['last_reset_date'] = date.today().isoformat()
        logger.info(f"📊 Daily stats reset at midnight (server time)")
    
    def _load_persisted_data(self):
        """Load persisted statistics and last heard data on startup, purge old data"""
        try:
            # Test write permissions early
            self._check_write_permissions()
            self._cleanup_temp_files()
            self._load_stats()
            self._load_last_heard()
            self._purge_old_data()
            logger.info("📁 Loaded persisted dashboard data")
        except PermissionError as e:
            logger.error(f"❌ No write permissions for persistence directory: {e}")
            logger.error("💡 Dashboard will run without persistence - restart data will not be saved")
            self._persistence_disabled = True
        except Exception as e:
            logger.warning(f"⚠️ Could not load persisted data: {e}")
    
    def _check_write_permissions(self):
        """Test write permissions to data directory"""
        try:
            # Create data directory if needed
            self._data_dir.mkdir(exist_ok=True)
            
            # Test write permissions with a temp file
            test_file = self._data_dir / ".write_test"
            test_file.write_text("test")
            test_file.unlink()
            
            # Set flag to enable persistence
            self._persistence_disabled = False
            
        except (PermissionError, OSError) as e:
            logger.error(f"❌ Cannot write to persistence directory {self._data_dir}: {e}")
            raise PermissionError(f"No write access to {self._data_dir}")
    
    def _cleanup_temp_files(self):
        """Remove any orphaned temp files from previous unclean shutdown"""
        try:
            if not self._data_dir.exists():
                return
                
            removed_count = 0
            for temp_file in self._data_dir.glob("*.tmp"):
                try:
                    temp_file.unlink()
                    removed_count += 1
                    logger.debug(f"🗑️ Removed orphaned temp file: {temp_file.name}")
                except OSError as e:
                    logger.warning(f"⚠️ Could not remove temp file {temp_file.name}: {e}")
            
            if removed_count > 0:
                logger.info(f"🧹 Cleaned up {removed_count} orphaned temp file(s) from previous session")
                
        except Exception as e:
            logger.warning(f"⚠️ Could not cleanup temp files: {e}")
    
    def _purge_old_data(self):
        """Remove old data files to prevent accumulation"""
        if self._persistence_disabled:
            return
            
        try:
            # Check if stats file is from a previous day and remove it
            if self._stats_file.exists():
                try:
                    with open(self._stats_file, 'r') as f:
                        saved_stats = json.load(f)
                    
                    today = date.today().isoformat()
                    if saved_stats.get('last_reset_date') != today:
                        self._stats_file.unlink()
                        logger.info("🗑️ Removed old stats file from previous day")
                except (json.JSONDecodeError, KeyError) as e:
                    # If we can't read it, try to remove it
                    try:
                        self._stats_file.unlink()
                        logger.info("🗑️ Removed corrupted stats file")
                    except OSError as remove_error:
                        logger.warning(f"⚠️ Could not remove corrupted stats file: {remove_error}")
                except OSError as e:
                    logger.warning(f"⚠️ Could not read/remove old stats file: {e}")
                
        except Exception as e:
            logger.warning(f"⚠️ Could not purge old data: {e}")
    
    def _load_stats(self):
        """Load statistics from file, validate date, reset if needed"""
        if not self._stats_file.exists():
            logger.debug("No stats file found, using fresh stats")
            return
        
        try:
            with open(self._stats_file, 'r') as f:
                saved_stats = json.load(f)
            
            # Check if stats are from today
            today = date.today().isoformat()
            if saved_stats.get('last_reset_date') == today:
                # Stats are still valid for today, load them
                self.stats.update(saved_stats)
                logger.info(f"📊 Loaded today's stats: {self.stats['total_calls_today']} calls, {self.stats['retransmitted_calls']} retransmitted")
            else:
                # Stats are from a previous day, keep fresh stats but log
                logger.info(f"📊 Previous day's stats found ({saved_stats.get('last_reset_date')}), using fresh daily stats")
        
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"⚠️ Could not parse stats file: {e}")
    
    def _load_last_heard(self):
        """Load last heard users, filtering out entries older than 4 hours (more aggressive)"""
        if not self._last_heard_file.exists():
            logger.debug("No last heard file found, starting fresh")
            return
        
        try:
            with open(self._last_heard_file, 'r') as f:
                saved_data = json.load(f)
            
            # More aggressive filtering - only keep entries from last 4 hours
            cutoff_time = datetime.now() - timedelta(hours=4)
            valid_users = []
            
            for user in saved_data.get('users', []):
                try:
                    last_seen = datetime.fromisoformat(user['last_seen'])
                    if last_seen > cutoff_time:
                        valid_users.append(user)
                except (ValueError, KeyError):
                    continue  # Skip malformed entries
            
            # Only keep data if we have recent users, otherwise start fresh
            if valid_users:
                self.last_heard = valid_users
                self.last_heard_stats = saved_data.get('stats', {})
                logger.info(f"📻 Loaded {len(valid_users)} recent users from last heard")
            else:
                logger.info("📻 No recent users found, starting fresh last heard list")
        
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"⚠️ Could not parse last heard file: {e}")
    
    def save_stats(self):
        """Save current statistics to file with atomic write (called only on shutdown)"""
        if self._persistence_disabled:
            logger.debug("📁 Persistence disabled - skipping stats save")
            return
            
        try:
            self._data_dir.mkdir(exist_ok=True)
            temp_file = self._stats_file.with_suffix('.tmp')
            
            with open(temp_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
            
            # Atomic operation - replace file only if write succeeded
            temp_file.rename(self._stats_file)
            logger.debug(f"📁 Saved stats to {self._stats_file}")
            
        except PermissionError as e:
            logger.error(f"❌ Permission denied saving stats: {e}")
            logger.error("💡 Dashboard will exit gracefully without saving persistence data")
        except Exception as e:
            logger.error(f"❌ Failed to save stats: {e}")
    
    def save_last_heard(self):
        """Save last heard users and stats to file with atomic write (called only on shutdown)"""
        if self._persistence_disabled:
            logger.debug("📁 Persistence disabled - skipping last heard save")
            return
            
        try:
            self._data_dir.mkdir(exist_ok=True)
            temp_file = self._last_heard_file.with_suffix('.tmp')
            
            data = {
                'timestamp': datetime.now().isoformat(),
                'users': self.last_heard,
                'stats': self.last_heard_stats
            }
            
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            # Atomic operation - replace file only if write succeeded
            temp_file.rename(self._last_heard_file)
            logger.debug(f"📁 Saved last heard to {self._last_heard_file}")
            
        except PermissionError as e:
            logger.error(f"❌ Permission denied saving last heard: {e}")
            logger.error("💡 Dashboard will exit gracefully without saving persistence data")
        except Exception as e:
            logger.error(f"❌ Failed to save last heard: {e}")
    
    def save_all_data(self):
        """Save all persistent data"""
        self.save_stats()
        self.save_last_heard()

state = DashboardState()


async def broadcast_hblink_status(connected: bool):
    """Broadcast HBlink4 connection status to all WebSocket clients"""
    state.hblink_connected = connected
    message = {
        'type': 'hblink_status',
        'data': {
            'connected': connected,
            'timestamp': datetime.now().isoformat()
        }
    }
    
    # Broadcast to all connected clients
    disconnected_clients = set()
    for client in state.websocket_clients:
        try:
            await client.send_json(message)
            
            # If HBlink just connected, also send full current state to ensure browser has everything
            if connected:
                await client.send_json({
                    'type': 'initial_state',
                    'data': {
                        'repeaters': list(state.repeaters.values()),
                        'repeater_details': state.repeater_details,
                        'outbounds': list(state.outbounds.values()),
                        'streams': list(state.streams.values()),
                        'events': list(state.events)[-50:],
                        'stats': state.stats,
                        'last_heard': state.last_heard,
                        'hblink_connected': state.hblink_connected
                    }
                })
        except Exception as e:
            logger.debug(f"Failed to send status to client: {e}")
            disconnected_clients.add(client)
    
    # Clean up disconnected clients
    state.websocket_clients -= disconnected_clients


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
        
        # Clear dashboard state on reconnect
        # HBlink4 will re-send all current repeaters via repeater_connected events
        logger.info("🔄 Clearing dashboard state - requesting HBlink4 state sync")
        state.repeaters.clear()
        state.outbounds.clear()
        state.streams.clear()
        
        # Set connection status synchronously (async broadcast may be delayed)
        state.hblink_connected = True
        
        # Send sync request to HBlink4 to trigger initial state send
        try:
            sync_request = json.dumps({'type': 'sync_request'}).encode('utf-8')
            length = len(sync_request)
            frame = length.to_bytes(4, byteorder='big') + sync_request
            transport.write(frame)
            logger.info("📤 Sent sync_request to HBlink4")
        except Exception as e:
            logger.error(f"Failed to send sync_request: {e}")
        
        # Notify all browser clients that HBlink4 is connected
        asyncio.create_task(broadcast_hblink_status(True))
    
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
        
        # Set connection status synchronously (async broadcast may be delayed)
        state.hblink_connected = False
        
        # Notify all browser clients that HBlink4 is disconnected
        asyncio.create_task(broadcast_hblink_status(False))


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
        
        # Clear dashboard state on reconnect
        # HBlink4 will re-send all current repeaters via repeater_connected events
        logger.info("🔄 Clearing dashboard state - requesting HBlink4 state sync")
        state.repeaters.clear()
        state.outbounds.clear()
        state.streams.clear()
        
        # Set connection status synchronously (async broadcast may be delayed)
        state.hblink_connected = True
        
        # Send sync request to HBlink4 to trigger initial state send
        try:
            sync_request = json.dumps({'type': 'sync_request'}).encode('utf-8')
            length = len(sync_request)
            frame = length.to_bytes(4, byteorder='big') + sync_request
            transport.write(frame)
            logger.info("📤 Sent sync_request to HBlink4")
        except Exception as e:
            logger.error(f"Failed to send sync_request: {e}")
        
        # Notify all browser clients that HBlink4 is connected
        asyncio.create_task(broadcast_hblink_status(True))
    
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
        
        # Set connection status synchronously (async broadcast may be delayed)
        state.hblink_connected = False
        
        # Notify all browser clients that HBlink4 is disconnected
        asyncio.create_task(broadcast_hblink_status(False))


class EventReceiver:
    """Receives events from hblink4 via TCP or Unix socket"""
    
    def __init__(self, transport='unix', host_ipv4='127.0.0.1', host_ipv6='::1',
                 port=8765, unix_socket='/tmp/hblink4.sock', disable_ipv6=False):
        """
        Initialize event receiver with transport abstraction
        
        Args:
            transport: 'tcp' or 'unix'
            host_ipv4: Listen address for IPv4 (for TCP)
            host_ipv6: Listen address for IPv6 (for TCP)
            port: Listen port (for TCP)
            unix_socket: Unix socket path (for Unix transport)
            disable_ipv6: Disable IPv6 (for networks with broken IPv6)
        """
        self.transport = transport.lower()
        self.host_ipv4 = host_ipv4
        self.host_ipv6 = host_ipv6 if not disable_ipv6 else None
        self.disable_ipv6 = disable_ipv6
        
        if disable_ipv6 and transport == 'tcp':
            logger.warning('⚠️  IPv6 disabled for event receiver - using IPv4 only')
        self.port = port
        self.unix_socket = unix_socket
        self.server = None
        self.server_v6 = None
    
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
        """Start TCP server on both IPv4 and IPv6"""
        # Start IPv4 listener
        if self.host_ipv4:
            try:
                self.server = await loop.create_server(
                    lambda: TCPProtocol(self.process_event),
                    self.host_ipv4, self.port,
                    family=socket.AF_INET
                )
                logger.info(f"✓ Listening for HBlink4 events via TCP on {self.host_ipv4}:{self.port} (IPv4)")
            except Exception as e:
                logger.error(f"✗ Failed to start IPv4 TCP listener: {e}")
        
        # Start IPv6 listener
        if self.host_ipv6:
            try:
                self.server_v6 = await loop.create_server(
                    lambda: TCPProtocol(self.process_event),
                    self.host_ipv6, self.port,
                    family=socket.AF_INET6
                )
                logger.info(f"✓ Listening for HBlink4 events via TCP on [{self.host_ipv6}]:{self.port} (IPv6)")
            except Exception as e:
                logger.error(f"✗ Failed to start IPv6 TCP listener: {e}")
    
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
                # Remove repeater from state immediately
                del state.repeaters[data['repeater_id']]
                logger.info(f"Repeater disconnected: {data['repeater_id']} ({data.get('callsign', 'UNKNOWN')}) - reason: {data.get('reason', 'unknown')}")
        
        elif event_type == 'repeater_details':
            # Store detailed repeater information (sent once on connection)
            state.repeater_details[data['repeater_id']] = {
                **data,
                'received_at': event['timestamp']
            }
            logger.debug(f"Repeater details received: {data['repeater_id']} - Pattern: {data.get('matched_pattern', 'Unknown')}")
        
        elif event_type == 'repeater_options_updated':
            # RPTO received - update TG lists in real-time
            if data['repeater_id'] in state.repeaters:
                state.repeaters[data['repeater_id']]['slot1_talkgroups'] = data.get('slot1_talkgroups', [])
                state.repeaters[data['repeater_id']]['slot2_talkgroups'] = data.get('slot2_talkgroups', [])
                state.repeaters[data['repeater_id']]['rpto_received'] = data.get('rpto_received', False)
                logger.info(f"Repeater options updated via RPTO: {data['repeater_id']}")
        
        elif event_type == 'stream_start':
            # Handle both repeater streams and outbound connection streams
            connection_type = data.get('connection_type', 'repeater')
            
            if connection_type == 'outbound':
                # Outbound stream - key by connection_name.slot
                key = f"{data['connection_name']}.{data['slot']}"
            else:
                repeater_id = data.get('repeater_id')
                if repeater_id is None:
                    logger.error("Stream start missing repeater_id: %s", event)
                    return
                # Repeater stream - key by repeater_id.slot (legacy behavior)
                key = f"{repeater_id}.{data['slot']}"
            
            # Look up callsign from user database
            src_id = data.get('rf_src') or data.get('src_id')  # Handle both field names
            callsign = user_database.get(src_id, '') if src_id else ''
            
            state.streams[key] = {
                **data,
                'callsign': callsign,  # Add callsign to stream data
                'start_time': event['timestamp'],
                'packets': 0,
                'duration': 0,
                'status': 'active'
            }
            
            # Count different stream types
            if not data.get('is_assumed', False):
                # RX streams: actual traffic being received (from repeaters OR outbound connections)
                # Only exclude TX streams (is_assumed=True)
                state.stats['total_calls_today'] += 1
                
                # Add/update user in last_heard immediately with "active" status
                # Only for actual received calls, not retransmitted calls
                if src_id:
                    # Build source display: "Name (originating_repeater_id)"
                    # For repeaters: callsign (repeater_id)
                    # For outbound: connection_name (remote_repeater_id)
                    if connection_type == 'outbound':
                        conn_name = data.get('connection_name', 'Unknown')
                        remote_rid = data.get('remote_repeater_id', 0)
                        source_name = f"{conn_name} ({remote_rid})"
                    else:
                        repeater_id = data.get('repeater_id', 0)
                        # Look up repeater callsign from state
                        repeater_info = state.repeaters.get(repeater_id, {})
                        repeater_callsign = repeater_info.get('callsign', '')
                        if repeater_callsign:
                            source_name = f"{repeater_callsign} ({repeater_id})"
                        else:
                            source_name = str(repeater_id)
                    
                    # Find existing entry or create new one
                    existing_idx = next((i for i, u in enumerate(state.last_heard) if u['radio_id'] == src_id), None)
                    user_entry = {
                        'radio_id': src_id,
                        'callsign': callsign,
                        'source_name': source_name,
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
            else:
                # TX streams: traffic being retransmitted to repeaters or outbound connections
                # Don't add to last_heard - these represent the same call being forwarded
                state.stats['retransmitted_calls'] += 1
            
            # Update repeater/outbound last activity
            if connection_type == 'outbound' and data['connection_name'] in state.outbounds:
                state.outbounds[data['connection_name']]['last_activity'] = event['timestamp']
            elif data.get('repeater_id') in state.repeaters:
                state.repeaters[data['repeater_id']]['last_activity'] = event['timestamp']
        
        elif event_type == 'stream_update':
            # Handle both repeater and outbound streams
            connection_type = data.get('connection_type', 'repeater')
            if connection_type == 'outbound':
                key = f"{data['connection_name']}.{data['slot']}"
            else:
                key = f"{data['repeater_id']}.{data['slot']}"
                
            if key in state.streams:
                state.streams[key]['packets'] = data['packets']
                state.streams[key]['duration'] = data['duration']
        
        elif event_type == 'stream_end':
            # Handle both repeater and outbound streams
            connection_type = data.get('connection_type', 'repeater')
            if connection_type == 'outbound':
                key = f"{data['connection_name']}.{data['slot']}"
            else:
                key = f"{data['repeater_id']}.{data['slot']}"
                
            if key in state.streams:
                # Stream ended and entering hang time (combined event)
                stream = state.streams[key]
                stream['status'] = 'hang_time'
                stream['packets'] = data.get('packet_count', data.get('packets', 0))
                stream['duration'] = data['duration']
                stream['end_reason'] = data.get('end_reason', 'unknown')
                stream['hang_time'] = data.get('hang_time', 0)
                
                # Only accumulate duration for RX streams (not assumed/TX streams or outbound)
                if not data.get('is_assumed', False) and connection_type != 'outbound':
                    state.stats['total_duration_today'] += data['duration']
                
                # Update last_heard entry to mark as no longer active (only for repeater RX streams)
                if connection_type != 'outbound':
                    src_id = stream.get('rf_src') or stream.get('src_id')
                    if src_id:
                        user_entry = next((u for u in state.last_heard if u['radio_id'] == src_id), None)
                        if user_entry:
                            user_entry['active'] = False
                            user_entry['last_heard'] = event['timestamp']
        
        elif event_type == 'hang_time_expired':
            # Hang time has expired, clear the slot (handle both repeater and outbound)
            connection_type = data.get('connection_type', 'repeater')
            if connection_type == 'outbound':
                key = f"{data['connection_name']}.{data['slot']}"
            else:
                key = f"{data['repeater_id']}.{data['slot']}"
                
            if key in state.streams:
                del state.streams[key]
                logger.debug(f"Hang time expired for {key}")
        
        elif event_type == 'outbound_connecting':
            # Outbound connection is attempting to connect
            conn_name = data['connection_name']
            state.outbounds[conn_name] = {
                **data,
                'connecting_at': event['timestamp'],
                'last_activity': event['timestamp'],
                'status': 'connecting'
            }
            logger.info(f"Outbound connection attempting: {conn_name} (radio_id={data.get('radio_id')})")
        
        elif event_type == 'outbound_connected':
            # Outbound connection established
            conn_name = data['connection_name']
            state.outbounds[conn_name] = {
                **data,
                'connected_at': event['timestamp'],
                'last_activity': event['timestamp'],
                'status': 'connected'
            }
            logger.info(f"Outbound connection established: {conn_name} (radio_id={data.get('radio_id')}) at {data.get('remote_address')}:{data.get('remote_port')}")
        
        elif event_type == 'outbound_disconnected':
            # Outbound connection lost - change to disconnected status but don't remove
            conn_name = data['connection_name']
            if conn_name in state.outbounds:
                state.outbounds[conn_name]['status'] = 'disconnected'
                state.outbounds[conn_name]['disconnected_at'] = event['timestamp']
                state.outbounds[conn_name]['disconnect_reason'] = data.get('reason', 'unknown')
                logger.info(f"Outbound connection disconnected: {conn_name} - reason: {data.get('reason', 'unknown')}")
        
        elif event_type == 'outbound_error':
            # Outbound connection error
            conn_name = data['connection_name']
            if conn_name in state.outbounds:
                state.outbounds[conn_name]['status'] = 'error'
                state.outbounds[conn_name]['error_message'] = data.get('error_message', 'Unknown error')
                state.outbounds[conn_name]['last_error'] = event['timestamp']
            else:
                # Connection failed before it was established
                state.outbounds[conn_name] = {
                    **data,
                    'status': 'error',
                    'error_message': data.get('error_message', 'Unknown error'),
                    'last_error': event['timestamp']
                }
            logger.warning(f"Outbound connection error: {conn_name} - {data.get('error_message', 'Unknown error')}")
        

        
        # Add to event log (only for user-facing on-air activity events)
        # Skip system events like repeater_connected, repeater_disconnected, hang_time_expired, repeater_keepalive
        # Skip TX/assumed streams from the events log (but they're still tracked in state.streams for repeater cards)
        if event_type in ['stream_start', 'stream_end']:
            # Only add RX streams to the events log (TX streams have is_assumed=True)
            if not data.get('is_assumed', False):
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


@app.get("/api/outbounds")
async def get_outbounds():
    """Get all outbound connections"""
    return {"outbounds": list(state.outbounds.values())}


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


@app.get("/api/repeater/{repeater_id}")
async def get_repeater_details(repeater_id: int):
    """Get detailed information about a specific repeater"""
    if repeater_id not in state.repeaters:
        return {"error": "Repeater not found"}, 404
    
    repeater = state.repeaters[repeater_id]
    details = state.repeater_details.get(repeater_id, {})
    
    # If no details event received yet, try to determine from config
    if not details or not details.get('matched_pattern'):
        # Try to load pattern from config
        config_path = Path(__file__).parent.parent / 'config' / 'config.json'
        try:
            with open(config_path) as f:
                config = json.load(f)
            
            # Import here to avoid circular dependencies
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from hblink4.access_control import RepeaterMatcher
            
            matcher = RepeaterMatcher(config)
            pattern = matcher.get_pattern_for_repeater(repeater_id, repeater.get('callsign'))
            
            if pattern:
                details['matched_pattern'] = pattern.name
                details['pattern_description'] = pattern.description
            else:
                details['matched_pattern'] = 'Default'
                details['pattern_description'] = 'Using default configuration'
        except Exception as e:
            logger.warning(f'Could not determine pattern for repeater {repeater_id}: {e}')
            details['matched_pattern'] = details.get('matched_pattern', 'Default')
            details['pattern_description'] = details.get('pattern_description', 'Using default configuration')
    
    # Calculate runtime statistics
    current_time = datetime.now().timestamp()
    uptime_seconds = int(current_time - repeater.get('connected_at', current_time))
    
    # Count streams for this repeater
    total_streams = len([e for e in state.events 
                        if e.get('type') == 'stream_start' 
                        and e.get('data', {}).get('repeater_id') == repeater_id])
    
    # Find active streams
    slot1_active = any(s.get('repeater_id') == repeater_id and s.get('slot') == 1 
                      and s.get('status') == 'active' 
                      for s in state.streams.values())
    slot2_active = any(s.get('repeater_id') == repeater_id and s.get('slot') == 2 
                      and s.get('status') == 'active' 
                      for s in state.streams.values())
    
    # Clean up IPv4-mapped IPv6 addresses
    address = repeater.get('address', '')
    if address.startswith('::ffff:'):
        address = address[7:]  # Remove '::ffff:' prefix
    
    # Build comprehensive response
    return {
        "repeater_id": repeater_id,
        "callsign": repeater.get('callsign', 'UNKNOWN'),
        "connection_type": repeater.get('connection_type', details.get('connection_type', 'unknown')),
        "connection": {
            "address": address,
            "connected_at": repeater.get('connected_at', 0),
            "uptime_seconds": uptime_seconds,
            "last_ping": repeater.get('last_ping', 0),
            "missed_pings": repeater.get('missed_pings', 0),
            "status": repeater.get('status', 'unknown')
        },
        "location": {
            "location": repeater.get('location', ''),
            "latitude": details.get('latitude', ''),
            "longitude": details.get('longitude', ''),
            "height": details.get('height', '')
        },
        "frequencies": {
            "rx_freq": repeater.get('rx_freq', ''),
            "tx_freq": repeater.get('tx_freq', ''),
            "tx_power": details.get('tx_power', ''),
            "colorcode": repeater.get('colorcode', ''),
            "slots": details.get('slots', '')
        },
        "access_control": {
            "connection_category": details.get('matched_pattern', 'Default'),
            "category_description": details.get('pattern_description', 'Using default configuration'),
            "rpto_received": repeater.get('rpto_received', False),
            "slot1_talkgroups": repeater.get('slot1_talkgroups') if 'slot1_talkgroups' in repeater else None,
            "slot2_talkgroups": repeater.get('slot2_talkgroups') if 'slot2_talkgroups' in repeater else None,
            "talkgroups_source": "RPTO" if repeater.get('rpto_received') else "Pattern/Config"
        },
        "metadata": {
            "description": details.get('description', ''),
            "url": details.get('url', ''),
            "software_id": repeater.get('software_id') or details.get('software_id', ''),
            "package_id": repeater.get('package_id') or details.get('package_id', '')
        },
        "statistics": {
            "total_streams_today": total_streams,
            "slot1_active": slot1_active,
            "slot2_active": slot2_active
        }
    }


# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket connection for real-time updates"""
    await websocket.accept()
    state.websocket_clients.add(websocket)
    logger.info(f"🌐 WebSocket client connected (total: {len(state.websocket_clients)})")
    
    # Log current state for debugging
    logger.info(f"📊 Sending initial_state: hblink_connected={state.hblink_connected}, repeaters={len(state.repeaters)}, streams={len(state.streams)}")
    
    # Send initial state
    await websocket.send_json({
        'type': 'initial_state',
        'data': {
            'repeaters': list(state.repeaters.values()),
            'repeater_details': state.repeater_details,
            'outbounds': list(state.outbounds.values()),
            'streams': list(state.streams.values()),
            'events': list(state.events)[-50:],
            'stats': state.stats,
            'last_heard': state.last_heard,
            'hblink_connected': state.hblink_connected
        }
    })
    
    try:
        while True:
            # Keep connection alive (client can send ping)
            data = await websocket.receive_text()
            if data == 'ping':
                await websocket.send_text('pong')
    except WebSocketDisconnect:
        logger.debug(f"WebSocket client disconnected (remaining: {len(state.websocket_clients) - 1})")
    except asyncio.CancelledError:
        # Expected during shutdown - don't log as error
        logger.debug(f"WebSocket task cancelled during shutdown")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Always clean up, regardless of how we exited
        state.websocket_clients.discard(websocket)


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
        host_ipv4=receiver_config.get('host_ipv4', '127.0.0.1'),
        host_ipv6=receiver_config.get('host_ipv6', '::1'),
        port=receiver_config.get('port', 8765),
        unix_socket=receiver_config.get('unix_socket', '/tmp/hblink4.sock'),
        disable_ipv6=receiver_config.get('disable_ipv6', False)
    )
    asyncio.create_task(receiver.start())
    asyncio.create_task(midnight_reset_task())
    logger.info("🚀 HBlink4 Dashboard started!")
    logger.info(f"📡 Event transport: {receiver_config.get('transport', 'unix').upper()}")
    logger.info("📊 Access dashboard at http://localhost:8080")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean shutdown - save data"""
    logger.info("💾 Dashboard shutting down, saving data...")
    save_persistent_data()
    logger.info("✅ Dashboard shutdown complete")


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


# ========== GRACEFUL SHUTDOWN HANDLING ==========

def save_persistent_data():
    """Save all persistent data on shutdown - called by signal handlers and atexit"""
    try:
        if state._persistence_disabled:
            logger.info("💾 Dashboard shutdown complete (persistence was disabled)")
            return
            
        state.save_all_data()
        logger.info("💾 Dashboard data saved on shutdown")
    except Exception as e:
        logger.error(f"❌ Failed to save data on shutdown: {e}")
        logger.info("💾 Dashboard shutdown complete (with save errors)")

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"📡 Received signal {signum}, shutting down...")
    # Just save and exit - let systemd/uvicorn handle the rest
    save_persistent_data()
    import sys
    sys.exit(0)

# Register shutdown handler
# Note: When running under systemd, don't override signal handlers
# Let uvicorn handle SIGTERM gracefully, which will trigger shutdown_event
if __name__ == "__main__":
    # Only register signal handlers when running directly (not via systemd/uvicorn service)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

atexit.register(save_persistent_data)  # Fallback for emergency exit


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "dashboard.server:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        access_log=False  # Disable access logging (reduces log clutter)
    )
