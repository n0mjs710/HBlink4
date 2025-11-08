#!/usr/bin/env python3
"""
Copyright (C) 2025 Cort Buffington, N0MJS

A complete architectural redesign of HBlink3, implementing a repeater-centric
approach to DMR server services. The HomeBrew DMR protocol is UDP-based, used for 
communication between DMR repeaters and servers.

License: GNU GPLv3
"""

import json
import logging
import logging.handlers
import pathlib
import ipaddress
from typing import Dict, Any, Optional, Tuple, Union, List, Set
from time import time
from random import randint
from hashlib import sha256

import signal
import asyncio

# Global configuration dictionary
CONFIG: Dict[str, Any] = {}
LOGGER = logging.getLogger(__name__)

import os
import sys

# Try package-relative imports first, fall back to direct imports
try:
    from .constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, MSTCL, DMRD,
        MSTNAK, MSTPONG, RPTPING, RPTACK, RPTP, RPTO, DMRA
    )
    from .access_control import RepeaterMatcher
    from .events import EventEmitter
    from .user_cache import UserCache
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, MSTCL, DMRD,
        MSTNAK, MSTPONG, RPTPING, RPTACK, RPTP, RPTO, DMRA
    )
    from access_control import RepeaterMatcher
    from events import EventEmitter
    from user_cache import UserCache

# Type definitions
# Address tuple can be IPv4 (host, port) or IPv6 (host, port, flowinfo, scopeid)
PeerAddress = Union[Tuple[str, int], Tuple[str, int, int, int]]

from dataclasses import dataclass, field

@dataclass
class OutboundConnectionConfig:
    """Configuration for an outbound connection to another server"""
    enabled: bool
    name: str
    address: str
    port: int
    radio_id: int
    password: str
    options: str = ""
    
    # Metadata fields with defaults
    callsign: str = ""
    rx_frequency: int = 0
    tx_frequency: int = 0
    power: int = 0
    colorcode: int = 1
    latitude: float = 0.0
    longitude: float = 0.0
    height: int = 0
    location: str = ""
    description: str = ""
    url: str = ""
    software_id: str = "HBlink4"
    package_id: str = "HBlink4 v2.0"
    
    def __post_init__(self):
        """Validate required fields"""
        if not self.name:
            raise ValueError("Outbound connection must have a name")
        if not self.address:
            raise ValueError(f"Outbound connection '{self.name}' must have an address")
        if not self.password:
            raise ValueError(f"Outbound connection '{self.name}' must have a password")
        if self.port <= 0 or self.port > 65535:
            raise ValueError(f"Outbound connection '{self.name}' has invalid port: {self.port}")

@dataclass
class StreamState:
    """Tracks an active DMR transmission stream"""
    repeater_id: bytes          # Repeater this stream is on
    rf_src: bytes            # RF source (3 bytes)
    dst_id: bytes            # Destination talkgroup/ID (3 bytes)
    slot: int                # Timeslot (1 or 2)
    start_time: float        # When transmission started
    last_seen: float         # Last packet received
    stream_id: bytes         # Unique stream identifier
    packet_count: int = 0    # Number of packets in this stream
    ended: bool = False      # True when stream has timed out but in hang time
    end_time: Optional[float] = None  # When stream ended (for hang time calculation)
    call_type: str = "unknown"  # Call type: "group", "private", "data", or "unknown"
    is_assumed: bool = False  # True if this is an assumed stream (forwarded to target, not received from it)
    target_repeaters: Optional[set] = None  # Cached set of repeater_ids approved for forwarding
    routing_cached: bool = False  # True once routing has been calculated
    
    def is_active(self, timeout: float = 2.0) -> bool:
        """Check if stream is still active (within timeout period)"""
        return (time() - self.last_seen) < timeout
    
    def is_in_hang_time(self, timeout: float, hang_time: float) -> bool:
        """Check if stream is in hang time (ended but slot reserved for same source)"""
        if not self.ended or not self.end_time:
            return False
        time_since_end = time() - self.end_time
        return time_since_end < hang_time

@dataclass
class OutboundState:
    """Data class for tracking outbound server connection state"""
    config: OutboundConnectionConfig  # Configuration object
    ip: str  # Resolved IP address
    port: int  # Remote port
    connected: bool = False
    authenticated: bool = False
    config_sent: bool = False  # RPTC sent and acked
    options_sent: bool = False  # RPTO sent
    last_ping: float = 0.0  # Last RPTPING sent
    last_pong: float = 0.0  # Last MSTPONG received
    missed_pongs: int = 0  # Consecutive missed pongs
    salt: int = 0  # Challenge salt from MSTCL
    connection_task: Optional[asyncio.Task] = None  # Connection management task
    transport: Optional[asyncio.DatagramTransport] = None  # UDP transport
    
    # Talkgroup filtering (parsed from config.options)
    # None = no restrictions (allow all), empty set = deny all
    slot1_talkgroups: Optional[set] = None
    slot2_talkgroups: Optional[set] = None
    
    # TDMA slot tracking - we're acting as a repeater with 2 timeslots
    # Each slot can only carry ONE talkgroup stream at a time (air interface constraint)
    slot1_stream: Optional['StreamState'] = None
    slot2_stream: Optional['StreamState'] = None
    
    @property
    def sockaddr(self) -> Tuple[str, int]:
        """Get socket address tuple"""
        return (self.ip, self.port)
    
    @property
    def is_alive(self) -> bool:
        """Check if connection is healthy (recent pong received)"""
        if not self.connected or not self.authenticated:
            return False
        # Allow 3 keepalive intervals before declaring dead
        keepalive = CONFIG.get('global', {}).get('ping_time', 5)
        return (time() - self.last_pong) < (keepalive * 3)
    
    def get_slot_stream(self, slot: int) -> Optional['StreamState']:
        """Get the active stream for a given slot (TDMA timeslot)"""
        if slot == 1:
            return self.slot1_stream
        elif slot == 2:
            return self.slot2_stream
        return None
    
    def set_slot_stream(self, slot: int, stream: Optional['StreamState']) -> None:
        """Set the active stream for a given slot (TDMA timeslot)"""
        if slot == 1:
            self.slot1_stream = stream
        elif slot == 2:
            self.slot2_stream = stream

@dataclass
class RepeaterState:
    """Data class for storing repeater state"""
    repeater_id: bytes
    ip: str
    port: int
    connected: bool = False
    authenticated: bool = False
    last_ping: float = field(default_factory=time)
    ping_count: int = 0
    missed_pings: int = 0
    salt: int = field(default_factory=lambda: randint(0, 0xFFFFFFFF))
    connection_state: str = 'login'  # States: login, config, connected
    last_rssi: int = 0
    rssi_count: int = 0
    
    # Metadata fields with defaults - stored as bytes to match protocol
    callsign: bytes = b''
    rx_freq: bytes = b''
    tx_freq: bytes = b''
    tx_power: bytes = b''
    colorcode: bytes = b''
    latitude: bytes = b''
    longitude: bytes = b''
    height: bytes = b''
    location: bytes = b''
    description: bytes = b''
    slots: bytes = b''
    url: bytes = b''
    software_id: bytes = b''
    package_id: bytes = b''
    
    # Talkgroup access control (stored as sets for O(1) lookup)
    # None = no restrictions (allow all), empty set = deny all, non-empty set = allow only those TGs
    slot1_talkgroups: Optional[set] = None
    slot2_talkgroups: Optional[set] = None
    rpto_received: bool = False  # True if repeater sent RPTO to override config TGs
    
    # Active stream tracking per slot
    slot1_stream: Optional[StreamState] = None
    slot2_stream: Optional[StreamState] = None
    
    # Cached decoded strings (for efficiency - decode once, use many times)
    _callsign_str: str = field(default='', init=False, repr=False)
    _location_str: str = field(default='', init=False, repr=False)
    _rx_freq_str: str = field(default='', init=False, repr=False)
    _tx_freq_str: str = field(default='', init=False, repr=False)
    _colorcode_str: str = field(default='', init=False, repr=False)
    
    @property
    def sockaddr(self) -> PeerAddress:
        """Get socket address tuple"""
        return (self.ip, self.port)
    
    def get_callsign_str(self) -> str:
        """Get decoded callsign string (cached)"""
        if not self._callsign_str and self.callsign:
            self._callsign_str = self.callsign.decode('utf-8', errors='ignore').strip()
        return self._callsign_str or 'UNKNOWN'
    
    def get_location_str(self) -> str:
        """Get decoded location string (cached)"""
        if not self._location_str and self.location:
            self._location_str = self.location.decode('utf-8', errors='ignore').strip()
        return self._location_str or 'Unknown'
    
    def get_rx_freq_str(self) -> str:
        """Get decoded RX frequency string (cached)"""
        if not self._rx_freq_str and self.rx_freq:
            self._rx_freq_str = self.rx_freq.decode('utf-8', errors='ignore').strip()
        return self._rx_freq_str
    
    def get_tx_freq_str(self) -> str:
        """Get decoded TX frequency string (cached)"""
        if not self._tx_freq_str and self.tx_freq:
            self._tx_freq_str = self.tx_freq.decode('utf-8', errors='ignore').strip()
        return self._tx_freq_str
    
    def get_colorcode_str(self) -> str:
        """Get decoded color code string (cached)"""
        if not self._colorcode_str and self.colorcode:
            self._colorcode_str = self.colorcode.decode('utf-8', errors='ignore').strip()
        return self._colorcode_str
    
    def get_slot_stream(self, slot: int) -> Optional[StreamState]:
        """Get the active stream for a given slot"""
        if slot == 1:
            return self.slot1_stream
        elif slot == 2:
            return self.slot2_stream
        return None
    
    def set_slot_stream(self, slot: int, stream: Optional[StreamState]) -> None:
        """Set the active stream for a given slot"""
        if slot == 1:
            self.slot1_stream = stream
        elif slot == 2:
            self.slot2_stream = stream

class HBProtocol(asyncio.DatagramProtocol):
    """UDP Implementation of HomeBrew DMR Server Protocol"""
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._repeaters: Dict[bytes, RepeaterState] = {}
        
        # Outbound connection state management (Phase 2)
        self._outbounds: Dict[str, 'OutboundState'] = {}  # keyed by connection name
        self._outbound_by_addr: Dict[Tuple[str, int], str] = {}  # (ip, port) -> name for O(1) routing
        self._outbound_ids: Set[int] = set()  # reserved IDs to prevent DoS
        
        self._config = CONFIG
        self._matcher = RepeaterMatcher(CONFIG)
        self._timeout_task = None
        self._stream_timeout_task = None
        self._user_cache_cleanup_task = None
        self._user_cache_send_task = None
        self._tasks = []  # List to track all async tasks

        self._port = None  # Store the port instance instead of transport
        
        # Initialize dashboard event emitter with config
        dashboard_config = CONFIG.get('dashboard', {})
        self._events = EventEmitter(
            enabled=dashboard_config.get('enabled', True),
            transport=dashboard_config.get('transport', 'unix'),
            host_ipv4=dashboard_config.get('host_ipv4', '127.0.0.1'),
            host_ipv6=dashboard_config.get('host_ipv6', '::1'),
            port=dashboard_config.get('port', 8765),
            unix_socket=dashboard_config.get('unix_socket', '/tmp/hblink4.sock'),
            disable_ipv6=dashboard_config.get('disable_ipv6', False),
            buffer_size=dashboard_config.get('buffer_size', 65536)
        )
        
        # Register reconnect callback to send current state when dashboard connects
        self._events.on_reconnect = self._send_initial_state
        
        # Active call tracking for contention management
        self._active_calls = 0  # Currently active forwarded calls
        
        # Track denied streams to avoid repeated logging
        # Key: (repeater_id, slot, stream_id), Value: timestamp of first denial
        self._denied_streams: Dict[tuple, float] = {}
        
        # Initialize user cache (mandatory for proper operation)
        user_cache_config = CONFIG.get('global', {}).get('user_cache', {})
        cache_timeout = user_cache_config.get('timeout', 600)
        if cache_timeout < 60:
            LOGGER.warning(f'user_cache timeout of {cache_timeout}s is too low, using minimum of 60s')
            cache_timeout = 60
        self._user_cache = UserCache(timeout_seconds=cache_timeout)
        LOGGER.info(f'User cache initialized with {cache_timeout}s timeout')
        
        # Cache for repeater_id bytes to int conversions (for logging efficiency)
        self._rid_cache: Dict[bytes, int] = {}
    
    @staticmethod
    def _normalize_addr(addr: PeerAddress) -> Tuple[str, int]:
        """
        Normalize address tuple to (ip, port) regardless of IPv4/IPv6 format.
        IPv4: (ip, port)
        IPv6: (ip, port, flowinfo, scopeid) -> (ip, port)
        """
        return (addr[0], addr[1])
    
    def _addr_matches(self, addr1: PeerAddress, addr2: PeerAddress) -> bool:
        """Compare two addresses, normalizing for IPv4/IPv6 differences"""
        return self._normalize_addr(addr1) == self._normalize_addr(addr2)
        
    # ========== HELPER METHODS ==========
    
    def _rid_to_int(self, repeater_id: bytes) -> int:
        """
        Convert repeater_id bytes to int with caching.
        Used for logging and event emission efficiency.
        """
        if repeater_id not in self._rid_cache:
            self._rid_cache[repeater_id] = int.from_bytes(repeater_id, 'big')
        return self._rid_cache[repeater_id]
    
    def _format_tg_display(self, tg_set: Optional[set]) -> str:
        """Format TG set for human-readable display (logging)"""
        if tg_set is None:
            return 'All'
        elif not tg_set:
            return 'None'
        else:
            return str(sorted(tg_set))
    
    def _format_tg_json(self, tg_set: Optional[set]) -> Optional[list]:
        """Format TG set for JSON serialization (events)"""
        if tg_set is None:
            return None
        elif not tg_set:
            return []
        else:
            return sorted(list(tg_set))
    
    def _prepare_repeater_event_data(self, repeater_id: bytes, repeater: RepeaterState) -> dict:
        """
        Prepare common repeater data dictionary for event emission.
        Centralizes the logic for converting repeater state to JSON-serializable format.
        """
        return {
            'repeater_id': self._rid_to_int(repeater_id),
            'callsign': repeater.get_callsign_str(),
            'location': repeater.get_location_str(),
            'address': f'{repeater.ip}:{repeater.port}',
            'rx_freq': repeater.get_rx_freq_str(),
            'tx_freq': repeater.get_tx_freq_str(),
            'colorcode': repeater.get_colorcode_str(),
            'slot1_talkgroups': self._format_tg_json(repeater.slot1_talkgroups),
            'slot2_talkgroups': self._format_tg_json(repeater.slot2_talkgroups),
            'rpto_received': repeater.rpto_received,
            'last_ping': repeater.last_ping,
            'missed_pings': repeater.missed_pings
        }
    
    def _load_repeater_tg_config(self, repeater_id: bytes, repeater: RepeaterState) -> None:
        """
        Load and cache TG configuration for a repeater.
        Converts config lists to sets for O(1) routing lookups.
        
        Note: Config must exist - repeater was already authenticated with this config.
        If this fails, it indicates a bug in authentication logic that must be fixed.
        """
        repeater_config = self._matcher.get_repeater_config(
            self._rid_to_int(repeater_id),
            repeater.get_callsign_str()
        )
        
        # Convert config to internal representation:
        # None stays None (allow all), lists become sets
        repeater.slot1_talkgroups = set(repeater_config.slot1_talkgroups) if repeater_config.slot1_talkgroups is not None else None
        repeater.slot2_talkgroups = set(repeater_config.slot2_talkgroups) if repeater_config.slot2_talkgroups is not None else None

    # ========== OUTBOUND CONNECTION METHODS (Phase 3) ==========
    
    def _parse_options(self, options: str) -> Tuple[Optional[set], Optional[set]]:
        """
        Parse Options= string into slot TG sets.
        Returns: (slot1_tgs, slot2_tgs)
        - None = allow all (*)
        - empty set = deny all (missing TS or empty)
        - non-empty set = allow only those TGs
        
        Format: "TS1=1,2,3;TS2=10,20" or "TS1=*;TS2=*" or "*"
        """
        if not options:
            return (None, None)  # Empty string = allow all (for backward compatibility)
        
        options = options.strip()
        if options == '*':
            return (None, None)  # Wildcard = allow all
        
        slot1_tgs = None  # Default: not specified (will become empty set if TS1 found but empty)
        slot2_tgs = None  # Default: not specified (will become empty set if TS2 found but empty)
        
        try:
            for part in options.split(';'):
                part = part.strip()
                if not part:
                    continue
                
                # Check for TS1=
                if part.startswith('TS1='):
                    tgs_str = part[4:].strip()  # Everything after 'TS1='
                    if tgs_str == '*':
                        slot1_tgs = None  # Wildcard on TS1
                    else:
                        slot1_tgs = set()  # TS1 specified, start with empty
                        if tgs_str:
                            slot1_tgs.update(int(tg.strip()) for tg in tgs_str.split(',') if tg.strip())
                
                # Check for TS2=
                elif part.startswith('TS2='):
                    tgs_str = part[4:].strip()  # Everything after 'TS2='
                    if tgs_str == '*':
                        slot2_tgs = None  # Wildcard on TS2
                    else:
                        slot2_tgs = set()  # TS2 specified, start with empty
                        if tgs_str:
                            slot2_tgs.update(int(tg.strip()) for tg in tgs_str.split(',') if tg.strip())
        
        except Exception as e:
            LOGGER.warning(f'Error parsing options "{options}": {e}')
            return (set(), set())  # Deny all on parse error
        
        # Convert None (not specified) to empty set (deny all) for any slot that wasn't mentioned
        if slot1_tgs is None and 'TS1=' not in options:
            slot1_tgs = set()  # TS1 not mentioned = deny all
        if slot2_tgs is None and 'TS2=' not in options:
            slot2_tgs = set()  # TS2 not mentioned = deny all
        
        return (slot1_tgs, slot2_tgs)
    
    async def _connect_outbound(self, config: OutboundConnectionConfig, loop=None):
        """
        Manage outbound connection lifecycle.
        Runs indefinitely, reconnecting on failure.
        """
        if loop is None:
            loop = asyncio.get_running_loop()
        
        keepalive_interval = CONFIG.get('global', {}).get('ping_time', 5)
        
        while True:
            try:
                # Phase 1: DNS Resolution
                LOGGER.info(f'[{config.name}] Resolving {config.address}...')
                try:
                    # Use getaddrinfo for DNS resolution
                    addr_info = await loop.getaddrinfo(
                        config.address, config.port,
                        family=0,  # AF_UNSPEC - allow IPv4 or IPv6
                        type=socket.SOCK_DGRAM
                    )
                    if not addr_info:
                        raise Exception(f'DNS resolution failed for {config.address}')
                    
                    # Use first result
                    family, socktype, proto, canonname, sockaddr = addr_info[0]
                    ip = sockaddr[0]
                    port = sockaddr[1]
                    LOGGER.info(f'[{config.name}] Resolved {config.address} → {ip}:{port}')
                except Exception as e:
                    LOGGER.error(f'[{config.name}] DNS resolution failed: {e}')
                    
                    # Emit error event
                    self._events.emit('outbound_error', {
                        'connection_name': config.name,
                        'radio_id': config.radio_id,
                        'remote_address': config.address,
                        'remote_port': config.port,
                        'error_message': f'DNS resolution failed: {e}'
                    })
                    
                    await asyncio.sleep(keepalive_interval)
                    continue
                
                # Phase 2: Create UDP endpoint
                try:
                    # Create a connected UDP socket (allows send() instead of sendto())
                    transport, protocol = await loop.create_datagram_endpoint(
                        lambda: asyncio.DatagramProtocol(),
                        remote_addr=(ip, port)
                    )
                    LOGGER.info(f'[{config.name}] UDP endpoint created to {ip}:{port}')
                except Exception as e:
                    LOGGER.error(f'[{config.name}] Failed to create UDP endpoint: {e}')
                    
                    # Emit error event
                    self._events.emit('outbound_error', {
                        'connection_name': config.name,
                        'radio_id': config.radio_id,
                        'remote_address': ip,
                        'remote_port': port,
                        'error_message': f'Failed to create UDP endpoint: {e}'
                    })
                    
                    await asyncio.sleep(keepalive_interval)
                    continue
                
                # Create outbound state
                slot1_tgs, slot2_tgs = self._parse_options(config.options)
                state = OutboundState(
                    config=config,
                    ip=ip,
                    port=port,
                    transport=transport,
                    slot1_talkgroups=slot1_tgs,
                    slot2_talkgroups=slot2_tgs
                )
                
                # Store in dictionaries
                self._outbounds[config.name] = state
                self._outbound_by_addr[(ip, port)] = config.name
                
                # Phase 3: Login (RPTL)
                our_id_bytes = config.radio_id.to_bytes(4, 'big')
                rptl_packet = RPTL + our_id_bytes
                transport.sendto(rptl_packet)
                LOGGER.info(f'[{config.name}] Sent RPTL (login) with ID {config.radio_id}')
                
                # Wait for MSTCL (challenge) with salt
                # State machine is driven by _handle_outbound_packet() receiving packets
                state.connected = True
                
                LOGGER.info(f'[{config.name}] Connection initiated, waiting for MSTCL...')
                
                # Phase 4: Keepalive loop
                while state.connected:
                    await asyncio.sleep(keepalive_interval)
                    
                    # Send RPTPING if authenticated
                    if state.authenticated:
                        ping_packet = RPTPING + our_id_bytes
                        state.transport.sendto(ping_packet)
                        state.last_ping = time()
                        LOGGER.debug(f'[{config.name}] Sent RPTPING')
                        
                        # Check for missed pongs
                        if state.last_pong > 0:
                            time_since_pong = time() - state.last_pong
                            if time_since_pong > (keepalive_interval * 3):
                                state.missed_pongs += 1
                                LOGGER.warning(f'[{config.name}] Missed pong #{state.missed_pongs} '
                                             f'({time_since_pong:.1f}s since last pong)')
                                if state.missed_pongs >= 3:
                                    LOGGER.error(f'[{config.name}] Connection lost (3 missed pongs)')
                                    state.connected = False
                                    
                                    # Emit disconnection event
                                    self._events.emit('outbound_disconnected', {
                                        'connection_name': config.name,
                                        'radio_id': config.radio_id,
                                        'remote_address': state.ip,
                                        'remote_port': state.port,
                                        'reason': 'Connection timeout (3 missed pongs)'
                                    })
                                    
                                    break
                
            except asyncio.CancelledError:
                LOGGER.info(f'[{config.name}] Connection task cancelled')
                break
            except Exception as e:
                LOGGER.error(f'[{config.name}] Connection error: {e}')
            finally:
                # Cleanup
                if config.name in self._outbounds:
                    state = self._outbounds[config.name]
                    if state.transport:
                        state.transport.close()
                    del self._outbounds[config.name]
                    if (state.ip, state.port) in self._outbound_by_addr:
                        del self._outbound_by_addr[(state.ip, state.port)]
                    LOGGER.info(f'[{config.name}] Cleaned up connection state')
            
            # Wait before reconnecting
            LOGGER.info(f'[{config.name}] Waiting {keepalive_interval}s before reconnect...')
            await asyncio.sleep(keepalive_interval)
    
    def _handle_outbound_packet(self, data: bytes, addr: tuple):
        """
        Handle packets received from outbound server connections.
        Implements client-side HomeBrew protocol state machine.
        """
        ip = addr[0]
        port = addr[1]
        addr_key = (ip, port)
        
        # Get outbound state
        conn_name = self._outbound_by_addr.get(addr_key)
        if not conn_name or conn_name not in self._outbounds:
            LOGGER.warning(f'Received packet from unknown outbound address {ip}:{port}')
            return
        
        state = self._outbounds[conn_name]
        _command = data[:4]
        
        try:
            # MSTCL - Challenge (response to RPTL)
            if _command == MSTCL:
                if len(data) < 8:
                    LOGGER.error(f'[{conn_name}] Invalid MSTCL packet length: {len(data)}')
                    return
                
                # Extract salt from challenge
                state.salt = int.from_bytes(data[4:8], 'big')
                LOGGER.info(f'[{conn_name}] Received MSTCL (challenge) with salt: {state.salt}')
                
                # Send RPTK (auth response)
                our_id_bytes = state.config.radio_id.to_bytes(4, 'big')
                salt_bytes = state.salt.to_bytes(4, 'big')
                calc_hash = bytes.fromhex(
                    sha256(salt_bytes + state.config.password.encode()).hexdigest()
                )
                rptk_packet = RPTK + our_id_bytes + calc_hash
                state.transport.sendto(rptk_packet)
                LOGGER.info(f'[{conn_name}] Sent RPTK (auth response)')
            
            # RPTACK - Acknowledgment
            elif _command == RPTACK:
                if not state.authenticated:
                    # Auth ACK
                    state.authenticated = True
                    LOGGER.info(f'[{conn_name}] Received RPTACK - Authentication successful')
                    
                    # Send RPTC (config)
                    self._send_outbound_config(state, (ip, port))
                elif not state.config_sent:
                    # Config ACK
                    state.config_sent = True
                    LOGGER.info(f'[{conn_name}] Received RPTACK - Config accepted')
                    
                    # Send RPTO (options) if configured
                    if state.config.options:
                        self._send_outbound_options(state, (ip, port))
                    else:
                        state.options_sent = True
                        LOGGER.info(f'[{conn_name}] No options configured, connection complete')
                elif not state.options_sent:
                    # Options ACK
                    state.options_sent = True
                    LOGGER.info(f'[{conn_name}] Received RPTACK - Options accepted, connection complete')
                    
                    # Emit connection established event
                    self._events.emit('outbound_connected', {
                        'connection_name': conn_name,
                        'radio_id': state.config.radio_id,
                        'remote_address': state.ip,
                        'remote_port': state.port,
                        'slot1_talkgroups': list(state.slot1_talkgroups) if state.slot1_talkgroups else None,
                        'slot2_talkgroups': list(state.slot2_talkgroups) if state.slot2_talkgroups else None
                    })
                else:
                    # Generic ACK
                    LOGGER.debug(f'[{conn_name}] Received RPTACK')
            
            # MSTNAK - Negative Acknowledgment
            elif _command == MSTNAK:
                LOGGER.error(f'[{conn_name}] Received MSTNAK - Connection rejected by server')
                state.connected = False
                
                # Emit error event
                self._events.emit('outbound_error', {
                    'connection_name': conn_name,
                    'radio_id': state.config.radio_id,
                    'remote_address': state.ip,
                    'remote_port': state.port,
                    'error_message': 'Connection rejected by server (MSTNAK)'
                })
            
            # MSTPONG - Keepalive response
            elif _command == MSTPONG:
                state.last_pong = time()
                state.missed_pongs = 0
                LOGGER.debug(f'[{conn_name}] Received MSTPONG')
            
            # MSTCL - Server disconnect
            elif _command[:5] == MSTCL:
                LOGGER.info(f'[{conn_name}] Received MSTCL - Server initiated disconnect')
                state.connected = False
                
                # Emit disconnection event
                self._events.emit('outbound_disconnected', {
                    'connection_name': conn_name,
                    'radio_id': state.config.radio_id,
                    'remote_address': state.ip,
                    'remote_port': state.port,
                    'reason': 'Server initiated disconnect'
                })
            
            # DMRD - DMR Data (voice/data from remote server)
            elif _command == DMRD:
                # Forward to local repeaters based on routing rules
                self._handle_outbound_dmr_data(data, state)
            
            else:
                try:
                    cmd_str = _command.decode('utf-8', errors='replace')
                except:
                    cmd_str = _command.hex()
                LOGGER.warning(f'[{conn_name}] Unknown command from outbound server: {cmd_str}')
                
        except Exception as e:
            LOGGER.error(f'[{conn_name}] Error processing outbound packet: {e}')
    
    def _send_outbound_config(self, state: OutboundState, addr: tuple):
        """Send RPTC (configuration) to outbound server"""
        config = state.config
        our_id_bytes = config.radio_id.to_bytes(4, 'big')
        
        # Build config packet (same format as repeater sends to us)
        # Pad/truncate strings to exact field lengths
        packet = RPTC + our_id_bytes
        packet += config.callsign.encode().ljust(8, b'\x00')[:8]
        packet += str(config.rx_frequency).encode().ljust(9, b'\x00')[:9]
        packet += str(config.tx_frequency).encode().ljust(9, b'\x00')[:9]
        packet += str(config.power).encode().ljust(2, b'\x00')[:2]
        packet += str(config.colorcode).encode().ljust(2, b'\x00')[:2]
        packet += str(config.latitude).encode().ljust(8, b'\x00')[:8]
        packet += str(config.longitude).encode().ljust(9, b'\x00')[:9]
        packet += str(config.height).encode().ljust(3, b'\x00')[:3]
        packet += config.location.encode().ljust(20, b'\x00')[:20]
        packet += config.description.encode().ljust(19, b'\x00')[:19]
        packet += b'3'  # Slots (placeholder)
        packet += config.url.encode().ljust(124, b'\x00')[:124]
        packet += config.software_id.encode().ljust(40, b'\x00')[:40]
        packet += config.package_id.encode().ljust(40, b'\x00')[:40]
        
        state.transport.sendto(packet)
        LOGGER.info(f'[{config.name}] Sent RPTC (config)')
    
    def _send_outbound_options(self, state: OutboundState, addr: tuple):
        """Send RPTO (options) to outbound server"""
        our_id_bytes = state.config.radio_id.to_bytes(4, 'big')
        options_bytes = state.config.options.encode().ljust(300, b'\x00')[:300]
        
        packet = RPTO + our_id_bytes + options_bytes
        state.transport.sendto(packet)
        LOGGER.info(f'[{state.config.name}] Sent RPTO (options): {state.config.options}')
    
    def _handle_outbound_dmr_data(self, data: bytes, outbound_state: OutboundState):
        """
        Handle DMR data received from an outbound server.
        Track stream state on outbound TDMA slots and forward to local repeaters.
        
        Args:
            data: Complete DMRD packet from outbound server
            outbound_state: State of the outbound connection
        """
        if len(data) < 55:
            LOGGER.warning(f'[{outbound_state.config.name}] Invalid DMRD packet length: {len(data)}')
            return
        
        # Extract packet information (same format as local repeater packets)
        _seq = data[4]
        _rf_src = data[5:8]
        _dst_id = data[8:11]
        _repeater_id = data[11:15]  # Source repeater ID from remote server
        _bits = data[15]
        _slot = 2 if (_bits & 0x80) else 1
        _call_type = (_bits & 0x40) >> 6
        _frame_type = (_bits & 0x30) >> 4
        _stream_id = data[16:20]
        
        tgid = int.from_bytes(_dst_id, 'big')
        src_id = int.from_bytes(_rf_src, 'big')
        remote_repeater_id = int.from_bytes(_repeater_id, 'big')
        _is_terminator = self._is_dmr_terminator(data, _frame_type)
        
        # Check if this talkgroup is allowed on this outbound connection
        allowed_tgs = outbound_state.slot1_talkgroups if _slot == 1 else outbound_state.slot2_talkgroups
        
        # None = allow all, empty set = deny all, non-empty set = specific TGs
        if allowed_tgs is not None and (not allowed_tgs or tgid not in allowed_tgs):
            LOGGER.debug(f'[{outbound_state.config.name}] Dropping packet for unauthorized TG {tgid} on slot {_slot}')
            return
        
        # Track stream state on outbound connection's TDMA slot (RX stream from remote server)
        current_stream = outbound_state.get_slot_stream(_slot)
        current_time = time()
        
        if not current_stream or current_stream.stream_id != _stream_id:
            # New RX stream from remote server - check if slot is busy with assumed (TX) stream
            if current_stream and current_stream.is_assumed and not current_stream.ended:
                # Slot busy with active TX stream - remote server wins, clear TX stream
                LOGGER.info(f'[{outbound_state.config.name}] TS{_slot} TX stream cleared by incoming RX stream')
                outbound_state.set_slot_stream(_slot, None)
                self._active_calls -= 1
            
            # Start new RX stream tracking
            dummy_id = outbound_state.config.radio_id.to_bytes(4, 'big')
            new_stream = StreamState(
                repeater_id=dummy_id,
                rf_src=_rf_src,
                dst_id=_dst_id,
                slot=_slot,
                start_time=current_time,
                last_seen=current_time,
                stream_id=_stream_id,
                packet_count=1,
                call_type="private" if _call_type else "group",
                is_assumed=False  # Real RX stream
            )
            outbound_state.set_slot_stream(_slot, new_stream)
            
            LOGGER.info(f'[{outbound_state.config.name}] RX stream started on TS{_slot}: '
                       f'src={src_id}, dst={tgid}, from remote repeater {remote_repeater_id}')
        else:
            # Update existing stream
            current_stream.last_seen = current_time
            current_stream.packet_count += 1
        
        # Handle terminator
        if _is_terminator and current_stream:
            dummy_id = outbound_state.config.radio_id.to_bytes(4, 'big')
            self._end_stream(current_stream, dummy_id, _slot, current_time, 'terminator')
        
        # Find local repeaters that should receive this traffic
        forwarded_count = 0
        for local_repeater_id, local_repeater in self._repeaters.items():
            # Only forward to connected repeaters
            if local_repeater.connection_state != 'connected':
                continue
            
            # Check if this local repeater allows this TG on this slot
            if not self._check_outbound_routing(local_repeater_id, _slot, tgid):
                continue
            
            # Check slot availability (don't hijack active streams)
            if self._is_slot_busy(local_repeater_id, _slot, _stream_id, _rf_src, _dst_id):
                continue
            
            # Forward the packet unchanged (repeater_id stays as remote source)
            self._send_packet(data, local_repeater.sockaddr)
            forwarded_count += 1
            
            # Track assumed stream state on local repeater
            self._update_assumed_stream(local_repeater, _slot, _rf_src, _dst_id,
                                       _stream_id, _is_terminator, remote_repeater_id)
        
        # Log forwarding at DEBUG level
        if forwarded_count > 0:
            LOGGER.debug(f'[{outbound_state.config.name}] Forwarded DMRD '
                        f'(slot {_slot}, src {src_id}, dst {tgid}) '
                        f'to {forwarded_count} local repeater(s)')

    
    # ========== END HELPER METHODS ==========
        
    def cleanup(self) -> None:
        """Send disconnect messages to all repeaters and cleanup resources."""
        LOGGER.info("Starting graceful shutdown...")
        
        # Send MSTCL to all connected repeaters
        if self._port:  # Only attempt to send if we have a port
            for repeater_id, repeater in self._repeaters.items():
                if repeater.connection_state == 'connected':
                    try:
                        LOGGER.info(f"Sending disconnect to repeater {self._rid_to_int(repeater_id)}")
                        # asyncio uses sendto() instead of write(data, addr)
                        self._port.sendto(MSTCL, repeater.sockaddr)
                    except Exception as e:
                        LOGGER.error(f"Error sending disconnect to repeater {self._rid_to_int(repeater_id)}: {e}")
        
        # Send RPTCL (disconnect) to all outbound connections
        for conn_name, outbound in list(self._outbounds.items()):
            if outbound.authenticated and outbound.transport:
                try:
                    LOGGER.info(f"Sending disconnect to outbound connection '{conn_name}'")
                    our_id_bytes = outbound.config.radio_id.to_bytes(4, 'big')
                    outbound.transport.sendto(RPTCL + our_id_bytes)
                    
                    # Emit disconnection event
                    self._events.emit('outbound_disconnected', {
                        'connection_name': conn_name,
                        'radio_id': outbound.config.radio_id,
                        'remote_address': outbound.ip,
                        'remote_port': outbound.port,
                        'reason': 'Server shutdown'
                    })
                except Exception as e:
                    LOGGER.error(f"Error sending disconnect to outbound '{conn_name}': {e}")
        
        # Cancel all outbound connection tasks
        for conn_name, outbound in self._outbounds.items():
            if outbound.connection_task and not outbound.connection_task.done():
                LOGGER.info(f"Cancelling connection task for '{conn_name}'")
                outbound.connection_task.cancel()

        # Give time for disconnects to be sent
        import time
        time.sleep(0.5)  # 500ms should be enough for UDP packets to be sent

    async def _run_periodic(self, interval: float, func, name: str):
        """
        Generic periodic task runner.
        
        Args:
            interval: Seconds between executions
            func: Synchronous function to call
            name: Task name for logging
        """
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    func()
                except Exception as e:
                    LOGGER.error(f"Error in {name}: {e}", exc_info=True)
        except asyncio.CancelledError:
            LOGGER.debug(f"{name} task cancelled")
            raise

    def connection_made(self, transport):
        """Called when the protocol starts"""
        # Get the port instance for sending data
        self.transport = transport
        self._port = self.transport
        """Called when transport is connected"""
        # Start timeout checker
        timeout_interval = CONFIG.get('timeout', {}).get('repeater', 30)
        self._tasks.append(
            asyncio.create_task(self._run_periodic(timeout_interval, self._check_repeater_timeouts, "repeater timeout checker"))
        )
        
        # Start stream timeout checker (check more frequently than repeater timeout)
        self._tasks.append(
            asyncio.create_task(self._run_periodic(1.0, self._check_stream_timeouts, "stream timeout checker"))
        )
        
        # Start user cache cleanup (fixed at 60s for optimal efficiency)
        self._tasks.append(
            asyncio.create_task(self._run_periodic(60, self._cleanup_user_cache, "user cache cleanup"))
        )
        LOGGER.info('Periodic tasks started (repeater timeout, stream timeout, user cache cleanup)')
        


    def connection_lost(self, exc):
        """Called when transport is disconnected"""
        # Cancel all periodic tasks
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        
        # Stop event emitter
        if hasattr(self, '_events') and self._events:
            self._events.close()
            
    def _check_repeater_timeouts(self):
        """Check for and handle repeater timeouts. Repeaters should send periodic RPTPING/RPTP."""
        current_time = time()
        timeout_duration = CONFIG.get('global', {}).get('timeout_duration', 30)  # 30 second default
        max_missed = CONFIG.get('global', {}).get('max_missed', 3)  # 3 missed pings default
        
        # Make a list to avoid modifying dict during iteration
        for repeater_id, repeater in list(self._repeaters.items()):
            if repeater.connection_state != 'connected':
                continue
                
            time_since_ping = current_time - repeater.last_ping
            
            if time_since_ping > timeout_duration:
                repeater.missed_pings += 1
                LOGGER.warning(f'Repeater {self._rid_to_int(repeater_id)} missed ping #{repeater.missed_pings}')
                
                # Emit event to update dashboard with missed ping count
                self._events.emit('repeater_connected', self._prepare_repeater_event_data(repeater_id, repeater))
                
                if repeater.missed_pings >= max_missed:
                    LOGGER.error(f'Repeater {self._rid_to_int(repeater_id)} timed out after {repeater.missed_pings} missed pings')
                    # Send NAK to trigger re-registration
                    self._send_nak(repeater_id, (repeater.ip, repeater.port), reason=f"Timeout after {repeater.missed_pings} missed pings")
                    self._remove_repeater(repeater_id, "timeout")
    
    def _end_stream(self, stream: StreamState, repeater_id: bytes, slot: int, 
                    current_time: float, end_reason: str) -> None:
        """
        Unified stream ending logic - marks stream as ended and emits events.
        
        Args:
            stream: The StreamState to end
            repeater_id: Repeater ID (bytes)
            slot: Slot number
            current_time: Current timestamp
            end_reason: Reason for ending ('timeout', 'terminator', 'fast_terminator')
        """
        if stream.ended:
            return  # Already ended
        
        # Mark stream as ended
        stream.ended = True
        stream.end_time = current_time
        duration = current_time - stream.start_time
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
        
        # Determine stream type for logging
        stream_type = "TX" if stream.is_assumed else "RX"
        
        # Build reason text
        if end_reason == 'terminator':
            reason_text = f'reason=terminator - entering hang time ({hang_time}s)'
        elif end_reason == 'fast_terminator':
            reason_text = f'reason=fast_terminator - entering hang time ({hang_time}s)'
        else:  # timeout
            reason_text = f'entering hang time ({hang_time}s)'
        
        # Log stream end (DEBUG for TX, INFO for RX)
        rid_int = self._rid_to_int(repeater_id)
        src_int = int.from_bytes(stream.rf_src, "big")
        dst_int = int.from_bytes(stream.dst_id, "big")
        
        if stream_type == "TX":
            LOGGER.debug(f'{stream_type} stream ended on repeater {rid_int} slot {slot}: '
                       f'src={src_int}, dst={dst_int}, '
                       f'duration={duration:.2f}s, packets={stream.packet_count}, {reason_text}')
        else:
            LOGGER.info(f'{stream_type} stream ended on repeater {rid_int} slot {slot}: '
                       f'src={src_int}, dst={dst_int}, '
                       f'duration={duration:.2f}s, packets={stream.packet_count}, {reason_text}')
        
        # Emit stream_end event for repeater card display
        # Dashboard will filter TX streams (is_assumed=True) from Recent Events log
        self._events.emit('stream_end', {
            'repeater_id': rid_int,
            'slot': slot,
            'src_id': src_int,
            'dst_id': dst_int,
            'duration': round(duration, 2),
            'packets': stream.packet_count,
            'end_reason': end_reason,
            'hang_time': hang_time,
            'call_type': stream.call_type,
            'is_assumed': stream.is_assumed
        })
        
        # Decrement active calls counter if this was an assumed (TX) stream
        if stream.is_assumed:
            self._active_calls -= 1
    
    def _check_slot_timeout(self, repeater_id: bytes, repeater: RepeaterState, slot: int, 
                           stream: StreamState, current_time: float, stream_timeout: float, 
                           hang_time: float) -> bool:
        """
        Check and handle timeout for a single slot stream.
        
        Returns:
            True if slot should be cleared, False otherwise
        """
        if not stream.is_active(stream_timeout):
            if not stream.ended:
                # Stream just ended - use unified ending logic
                self._end_stream(stream, repeater_id, slot, current_time, 'timeout')
                return False  # Don't clear yet - entering hang time
                
            elif not stream.is_in_hang_time(stream_timeout, hang_time):
                # Hang time expired - clear the slot
                hang_duration = current_time - stream.end_time if stream.end_time else 0
                stream_type = "TX" if stream.is_assumed else "RX"
                LOGGER.debug(f'{stream_type} hang time completed on repeater {self._rid_to_int(repeater_id)} slot {slot}: '
                           f'src={int.from_bytes(stream.rf_src, "big")}, '
                           f'dst={int.from_bytes(stream.dst_id, "big")}, '
                           f'hang_duration={hang_duration:.2f}s')
                # Emit hang_time_expired event so dashboard clears the slot
                self._events.emit('hang_time_expired', {
                    'repeater_id': self._rid_to_int(repeater_id),
                    'slot': slot
                })
                return True  # Clear the slot
        
        return False  # Stream still active or in hang time
    
    def _check_stream_timeouts(self):
        """Check for and clean up stale streams on all repeaters"""
        current_time = time()
        stream_timeout = CONFIG.get('global', {}).get('stream_timeout', 2.0)
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 3.0)
        
        # Check for dashboard sync requests (non-blocking)
        if hasattr(self, '_events') and self._events:
            self._events.check_for_sync_request()
        
        for repeater_id, repeater in self._repeaters.items():
            if repeater.connection_state != 'connected':
                continue
            
            # Check slot 1
            if repeater.slot1_stream:
                if self._check_slot_timeout(repeater_id, repeater, 1, repeater.slot1_stream,
                                           current_time, stream_timeout, hang_time):
                    repeater.slot1_stream = None
            
            # Check slot 2
            if repeater.slot2_stream:
                if self._check_slot_timeout(repeater_id, repeater, 2, repeater.slot2_stream,
                                           current_time, stream_timeout, hang_time):
                    repeater.slot2_stream = None
        
        # Cleanup old denied stream entries (older than 10 seconds)
        denied_cutoff = current_time - 10.0
        self._denied_streams = {k: v for k, v in self._denied_streams.items() if v > denied_cutoff}
    
    def _cleanup_user_cache(self):
        """Periodic cleanup of expired user cache entries"""
        if self._user_cache:
            removed = self._user_cache.cleanup()
            if removed > 0:
                LOGGER.debug(f'User cache cleanup: removed {removed} expired entries')
    

    def _send_initial_state(self):
        """Send current state of all connected repeaters to dashboard (called on reconnect)"""
        try:
            for repeater_id, repeater in self._repeaters.items():
                if repeater.connected and repeater.connection_state == 'connected':
                    # Emit repeater_connected for each already-connected repeater
                    self._events.emit('repeater_connected', self._prepare_repeater_event_data(repeater_id, repeater))
            
            LOGGER.info(f'📤 Sent initial state: {len([r for r in self._repeaters.values() if r.connected])} connected repeaters')
        except Exception as e:
            LOGGER.error(f'Error sending initial state: {e}')
    
    def _check_inbound_routing(self, repeater_id: bytes, slot: int, tgid: int) -> bool:
        """
        Check if a repeater is allowed to send traffic on this TS/TGID.
        
        Uses cached TG sets in RepeaterState for O(1) lookup.
        
        Args:
            repeater_id: Repeater ID to check
            slot: Timeslot (1 or 2)
            tgid: Talkgroup ID
            
        Returns:
            True if traffic is allowed, False otherwise
        """
        # Get repeater state
        repeater = self._repeaters.get(repeater_id)
        if not repeater:
            return False
        
        # Get slot-specific talkgroup set from repeater state
        allowed_tgids = repeater.slot1_talkgroups if slot == 1 else repeater.slot2_talkgroups
        
        # None means no restrictions (allow all)
        if allowed_tgids is None:
            return True
        
        # Empty set means deny all
        if not allowed_tgids:
            return False
        
        # O(1) set membership check
        return tgid in allowed_tgids
    
    def _check_outbound_routing(self, repeater_id: bytes, slot: int, tgid: int) -> bool:
        """
        Check if traffic should be forwarded to this repeater on this TS/TGID.
        
        Uses cached TG sets in RepeaterState for O(1) lookup.
        Same set and logic as inbound - symmetric routing.
        
        Args:
            repeater_id: Repeater ID to check
            slot: Timeslot (1 or 2)  
            tgid: Talkgroup ID
            
        Returns:
            True if traffic should be forwarded, False otherwise
        """
        # Get repeater state
        repeater = self._repeaters.get(repeater_id)
        if not repeater:
            return False
        
        # Get slot-specific talkgroup set from repeater state
        allowed_tgids = repeater.slot1_talkgroups if slot == 1 else repeater.slot2_talkgroups
        
        # None means no restrictions (allow all)
        if allowed_tgids is None:
            return True
        
        # Empty set means deny all
        if not allowed_tgids:
            return False
        
        # O(1) set membership check
        return tgid in allowed_tgids
    
    def _is_slot_busy(self, repeater_id: bytes, slot: int, stream_id: bytes, 
                     rf_src: bytes = None, dst_id: bytes = None) -> bool:
        """
        Check if a slot is busy with a different stream (contention check).
        
        Args:
            repeater_id: Repeater ID to check
            slot: Timeslot to check
            stream_id: Current stream ID (to allow same stream through)
            rf_src: Source subscriber ID (optional, for hang time check)
            dst_id: Destination TGID (optional, for hang time check)
            
        Returns:
            True if slot is busy with different stream, False if available
        """
        repeater = self._repeaters.get(repeater_id)
        if not repeater:
            return False
        
        # Get the slot's current stream
        current_stream = repeater.get_slot_stream(slot)
        if not current_stream:
            return False  # No stream, slot is free
        
        # Check if it's the same stream
        if current_stream.stream_id == stream_id:
            return False  # Same stream, not busy
        
        # Check if stream has ended and is in hang time
        current_time = time()
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
        
        if current_stream.end_time:
            # Stream has ended, check hang time
            time_since_end = current_time - current_stream.end_time
            if time_since_end > hang_time:
                return False  # Hang time expired, slot is free
            
            # Still in hang time - hang time protects the TALKGROUP conversation
            # Allow: 1) Any user on same talkgroup (conversation continues)
            #        2) Original user switching to different talkgroup (special case)
            # Block: Different user trying to use different talkgroup (hijacking)
            if rf_src and dst_id:
                # Same user can always break through (any talkgroup)
                if current_stream.rf_src == rf_src:
                    return False  # Same user, allow through
                # Different user - check if same talkgroup
                if current_stream.dst_id == dst_id:
                    return False  # Different user, but same TG conversation - allow
                # Different user AND different talkgroup = blocked
                # This is the hijacking case we prevent
        
        # Slot is busy with a different active stream or protected by hang time
        return True

    def datagram_received(self, data: bytes, addr: tuple):
        """Handle received UDP datagram"""
        # Handle both IPv4 (ip, port) and IPv6 (ip, port, flowinfo, scopeid) address formats
        ip = addr[0]
        port = addr[1]
        
        # Check if this is from an outbound connection (O(1) lookup)
        addr_key = (ip, port)
        if addr_key in self._outbound_by_addr:
            # Route to outbound handler
            self._handle_outbound_packet(data, addr)
            return
        
        # Debug log the raw packet
        #LOGGER.debug(f'Raw packet from {ip}:{port}: {data.hex()}')
            
        _command = data[:4]
        # Per-packet logging - only enable for heavy troubleshooting
        #LOGGER.debug(f'Command bytes: {_command}')
        
        try:
            # Extract repeater_id based on packet type
            repeater_id = None
            if _command == DMRD:
                repeater_id = data[11:15]
            elif _command == RPTP:
                repeater_id = data[7:11]
            elif _command == RPTL:
                repeater_id = data[4:8]
            elif _command == RPTK:
                repeater_id = data[4:8]
            elif _command == RPTO:
                repeater_id = data[4:8]
            elif _command == DMRA:
                repeater_id = data[4:8]
            elif _command == RPTC:
                if data[:5] == RPTCL:
                    repeater_id = data[5:9]
                else:
                    repeater_id = data[4:8]
                
            if repeater_id:
                # Per-packet logging - only enable for heavy troubleshooting
                #LOGGER.debug(f'Packet received: cmd={_command}, repeater_id={self._rid_to_int(repeater_id)}, addr={addr}')
                pass  # Keep pass for valid if statement syntax
            else:
                # Unknown packet type - log full details for investigation
                try:
                    cmd_str = _command.decode('utf-8', errors='replace')
                except:
                    cmd_str = _command.hex()
                LOGGER.warning(f'⚠️  UNKNOWN PACKET TYPE from {ip}:{port}')
                LOGGER.warning(f'    Command: {cmd_str} (hex: {_command.hex()})')
                LOGGER.warning(f'    Full packet (first 60 bytes): {data[:60].hex()}')
                LOGGER.warning(f'    Packet length: {len(data)} bytes')
                return

            # If repeater is not registered and this is not a login or auth packet, send NAK and return
            if repeater_id and repeater_id not in self._repeaters:
                if _command not in [RPTL, RPTK]:
                    self._send_nak(repeater_id, addr, reason="Repeater not registered")
                    return

            # Update ping time for connected repeaters
            if repeater_id and repeater_id in self._repeaters:
                repeater = self._repeaters[repeater_id]
                if repeater.connection_state == 'connected':
                    repeater.last_ping = time()
                    # If missed_pings is being cleared, notify dashboard
                    if repeater.missed_pings > 0:
                        repeater.missed_pings = 0
                        self._events.emit('repeater_connected', self._prepare_repeater_event_data(repeater_id, repeater))
                    else:
                        repeater.missed_pings = 0

            # Process the packet
            if _command == DMRD:
                self._handle_dmr_data(data, addr)
            elif _command == RPTL:
                LOGGER.debug(f'Received RPTL from {ip}:{port} - Repeater Login Request')
                self._handle_repeater_login(repeater_id, addr)
            elif len(data) == 4:  # Special case: raw repeater ID login
                # Try to interpret as a raw repeater ID
                LOGGER.debug(f'Received possible raw repeater ID login from {ip}:{port}')
                self._handle_repeater_login(data, addr)
            elif _command == RPTK:
                LOGGER.debug(f'Received RPTK from {ip}:{port} - Authentication Response')
                self._handle_auth_response(repeater_id, data[8:], addr)
            elif _command == RPTC:
                if data[:5] == RPTCL:
                    LOGGER.debug(f'Received RPTCL from {ip}:{port} - Disconnect Request')
                    self._handle_disconnect(repeater_id, addr)
                else:
                    LOGGER.debug(f'Received RPTC from {ip}:{port} - Configuration Data')
                    self._handle_config(data, addr)
            elif _command[:4] == RPTP:  # Check just RPTP prefix since that's enough to identify RPTPING
                LOGGER.debug(f'Received RPTPING from {ip}:{port} - Repeater Keepalive')
                self._handle_ping(repeater_id, addr)
            elif _command == RPTO:
                LOGGER.info(f'Received RPTO from {ip}:{port} - Options/TG Configuration')
                self._handle_options(repeater_id, data[8:], addr)
            elif _command == DMRA:
                LOGGER.debug(f'Received DMRA from {ip}:{port} - DMR Talker Alias (packet length: {len(data)})')
                if repeater_id:
                    self._handle_talker_alias(repeater_id, data[8:], addr)
                else:
                    LOGGER.warning(f'DMRA packet from {ip}:{port} has no repeater_id - packet hex: {data[:20].hex()}')
            else:
                # Try to decode the command as ASCII for better logging
                try:
                    cmd_str = _command.decode('utf-8', errors='replace')
                except:
                    cmd_str = _command.hex()
                LOGGER.warning(f'Unknown command received from {ip}:{port}: {cmd_str} (hex: {_command.hex()})')
        except Exception as e:
            LOGGER.error(f'Error processing datagram from {ip}:{port}: {str(e)}')

    def _validate_repeater(self, repeater_id: bytes, addr: PeerAddress) -> Optional[RepeaterState]:
        """Validate repeater state and address"""
        if repeater_id not in self._repeaters:
            # Per-packet logging - only enable for heavy troubleshooting
            #LOGGER.debug(f'Repeater {self._rid_to_int(repeater_id)} not found in _repeaters dict')
            self._send_nak(repeater_id, addr, reason="Repeater not registered")
            return None
            
        repeater = self._repeaters[repeater_id]
        # Per-packet logging - only enable for heavy troubleshooting
        #LOGGER.debug(f'Validating repeater {self._rid_to_int(repeater_id)}: state="{repeater.connection_state}", stored_addr={repeater.sockaddr}, incoming_addr={addr}')
        
        if not self._addr_matches(repeater.sockaddr, addr):
            LOGGER.warning(f'Message from wrong IP for repeater {self._rid_to_int(repeater_id)}')
            self._send_nak(repeater_id, addr, reason="Message from incorrect IP address")
            return None
            
        return repeater
    
    def _handle_stream_start(self, repeater: RepeaterState, rf_src: bytes, dst_id: bytes, 
                             slot: int, stream_id: bytes, call_type_bit: int = 1) -> bool:
        """
        Handle the start of a new stream on a repeater slot.
        Returns True if the stream can proceed, False if there's a contention.
        """
        # Check if this is a unit/private call (call_type_bit == 1)
        if call_type_bit == 1:
            LOGGER.info(f'UNIT CALL received on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                       f'src={int.from_bytes(rf_src, "big")}, dst={int.from_bytes(dst_id, "big")}, '
                       f'stream_id={stream_id.hex()} [NOT ROUTED - unit call handling not yet implemented]')
            return False  # Reject the stream - don't process unit calls yet
        
        current_stream = repeater.get_slot_stream(slot)
        current_time = time()
        fast_tg_switch = False  # Track if this is a fast talkgroup switch
        
        # Check if there's already an active stream on this slot
        if current_stream:
            # Same stream continuing (same stream_id)
            if current_stream.stream_id == stream_id:
                return True
            
            # Special case: If current stream is an ACTIVE assumed (TX) stream and we're receiving
            # a real (RX) stream from the same repeater, the repeater wins.
            # Remove this repeater from any active route-caches to stop wasting bandwidth.
            # Note: Ended assumed streams should go through normal hang time logic instead.
            if current_stream.is_assumed and not current_stream.ended:
                LOGGER.info(f'Repeater {self._rid_to_int(repeater.repeater_id)} slot {slot} '
                           f'starting RX while we have active assumed TX stream - repeater wins, '
                           f'removing from active route-caches')
                
                # Remove this repeater from all active stream route-caches
                for other_repeater in self._repeaters.values():
                    for other_slot in [1, 2]:
                        other_stream = other_repeater.get_slot_stream(other_slot)
                        if (other_stream and 
                            other_stream.routing_cached and 
                            other_stream.target_repeaters and
                            repeater.repeater_id in other_stream.target_repeaters):
                            other_stream.target_repeaters.discard(repeater.repeater_id)
                            LOGGER.debug(f'Removed repeater {self._rid_to_int(repeater.repeater_id)} '
                                       f'from route-cache of stream on repeater '
                                       f'{self._rid_to_int(other_repeater.repeater_id)} slot {other_slot}')
                
                # Clear the assumed stream - real stream takes precedence
                # Fall through to create new real stream
            # Check if stream is in hang time
            elif current_stream.ended:
                # Stream has ended but is in hang time
                # Hang time protects the TALKGROUP conversation from being hijacked
                # Allow: 1) Any user continuing same talkgroup conversation
                #        2) Original user switching to different talkgroup (special case)
                # Block: Different user trying different talkgroup (hijacking)
                
                # Same user can always continue (any talkgroup)
                if current_stream.rf_src == rf_src:
                    if current_stream.dst_id == dst_id:
                        LOGGER.info(f'Same user continuing conversation on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot} '
                                   f'during hang time: src={int.from_bytes(rf_src, "big")}, dst={int.from_bytes(dst_id, "big")}')
                    else:
                        LOGGER.info(f'Same user switching talkgroup on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot} '
                                   f'during hang time: src={int.from_bytes(rf_src, "big")}, '
                                   f'old_dst={int.from_bytes(current_stream.dst_id, "big")}, '
                                   f'new_dst={int.from_bytes(dst_id, "big")}')
                        fast_tg_switch = True  # Mark as fast talkgroup switch
                    # Allow by falling through to create new stream
                # Different user - check if same talkgroup
                elif current_stream.dst_id == dst_id:
                    LOGGER.info(f'Different user joining conversation on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot} '
                               f'during hang time: old_src={int.from_bytes(current_stream.rf_src, "big")}, '
                               f'new_src={int.from_bytes(rf_src, "big")}, dst={int.from_bytes(dst_id, "big")}')
                    # Allow by falling through to create new stream
                else:
                    # Different user AND different talkgroup = hijacking attempt
                    LOGGER.warning(f'Hang time hijacking blocked on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                                  f'slot reserved for TG {int.from_bytes(current_stream.dst_id, "big")}, '
                                  f'denied src={int.from_bytes(rf_src, "big")} attempting TG {int.from_bytes(dst_id, "big")}')
                    return False
            else:
                # Active stream - different stream_id means contention
                LOGGER.warning(f'Stream contention on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                              f'existing stream (src={int.from_bytes(current_stream.rf_src, "big")}, '
                              f'dst={int.from_bytes(current_stream.dst_id, "big")}) '
                              f'vs new stream (src={int.from_bytes(rf_src, "big")}, '
                              f'dst={int.from_bytes(dst_id, "big")})')
                
                # Deny the new stream - first come, first served
                return False
        
        # Check if this repeater is allowed to send traffic on this TS/TGID (inbound routing)
        tgid = int.from_bytes(dst_id, 'big')
        if not self._check_inbound_routing(repeater.repeater_id, slot, tgid):
            # Track denied streams to avoid logging every packet
            denial_key = (repeater.repeater_id, slot, stream_id)
            current_time = time()
            
            # Only log if this is the first packet of this denied stream
            if denial_key not in self._denied_streams:
                allowed_tgids = repeater.slot1_talkgroups if slot == 1 else repeater.slot2_talkgroups
                LOGGER.warning(f'Inbound routing denied: repeater={int.from_bytes(repeater.repeater_id, "big")} '
                              f'TS{slot}/TG{tgid} not in allowed list {sorted(allowed_tgids)}')
                
                # Add to denied cache
                self._denied_streams[denial_key] = current_time
            
            return False
        
        # Calculate forwarding targets (once per stream, not per packet!)
        target_repeaters = self._calculate_stream_targets(
            repeater.repeater_id, slot, dst_id, stream_id, rf_src
        )
        
        # No active stream, start a new one with routing cache
        new_stream = StreamState(
            repeater_id=repeater.repeater_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            start_time=current_time,
            last_seen=current_time,
            stream_id=stream_id,
            packet_count=1,
            call_type="private" if call_type_bit else "group",
            target_repeaters=target_repeaters,
            routing_cached=True
        )
        
        repeater.set_slot_stream(slot, new_stream)
        
        # Log stream start with fast talkgroup switch indicator and target count
        if fast_tg_switch:
            LOGGER.info(f'RX stream started on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                       f'src={int.from_bytes(rf_src, "big")}, dst={int.from_bytes(dst_id, "big")}, '
                       f'stream_id={stream_id.hex()}, targets={len(target_repeaters)} [FAST TG SWITCH]')
        else:
            LOGGER.info(f'RX stream started on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                       f'src={int.from_bytes(rf_src, "big")}, dst={int.from_bytes(dst_id, "big")}, '
                       f'stream_id={stream_id.hex()}, targets={len(target_repeaters)}')
        
        # Emit stream_start event
        self._events.emit('stream_start', {
            'repeater_id': int.from_bytes(repeater.repeater_id, 'big'),
            'slot': slot,
            'src_id': int.from_bytes(rf_src, 'big'),
            'dst_id': int.from_bytes(dst_id, 'big'),
            'stream_id': stream_id.hex(),
            'call_type': new_stream.call_type
        })
        
        # Update user cache (for "last heard" and private call routing)
        if self._user_cache:
            src_id = int.from_bytes(rf_src, 'big')
            repeater_id = int.from_bytes(repeater.repeater_id, 'big')
            dst = int.from_bytes(dst_id, 'big')
            self._user_cache.update(
                radio_id=src_id,
                repeater_id=repeater_id,
                callsign='',  # Callsign lookup handled by dashboard
                slot=slot,
                talkgroup=dst
            )
        
        return True
    
    def _handle_stream_packet(self, repeater: RepeaterState, rf_src: bytes, dst_id: bytes,
                              slot: int, stream_id: bytes, call_type_bit: int = 1) -> bool:
        """
        Handle a packet for an ongoing stream.
        Returns True if the packet is valid for the current stream, False otherwise.
        """
        current_stream = repeater.get_slot_stream(slot)
        
        if not current_stream:
            # No active stream - this is a new stream
            return self._handle_stream_start(repeater, rf_src, dst_id, slot, stream_id, call_type_bit)
        
        # Check if this packet belongs to the current stream
        if current_stream.stream_id != stream_id:
            # Different stream - potential contention
            # But check if old stream is stale (>200ms since last packet)
            # This provides fast terminator detection when operators key up quickly
            current_time = time()
            time_since_last_packet = current_time - current_stream.last_seen
            
            # Only use fast terminator for active streams that never got a proper terminator
            # If stream is already ended (in hang time), skip to hang time check
            if not current_stream.ended and time_since_last_packet > 0.2:  # 200ms threshold
                # Old stream appears terminated - use unified ending logic
                # Log the fast terminator detection first
                LOGGER.info(f'Fast terminator: stream on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot} '
                           f'ended via inactivity ({time_since_last_packet*1000:.0f}ms since last packet): '
                           f'src={int.from_bytes(current_stream.rf_src, "big")}, '
                           f'dst={int.from_bytes(current_stream.dst_id, "big")}, '
                           f'duration={(current_time - current_stream.start_time):.2f}s, packets={current_stream.packet_count}')
                
                # Now use unified ending logic
                self._end_stream(current_stream, repeater.repeater_id, slot, current_time, 'fast_terminator')
                
                # Don't clear the stream - let _handle_stream_start check hang time
                # It will create the new stream and replace this one if allowed
                return self._handle_stream_start(repeater, rf_src, dst_id, slot, stream_id, call_type_bit)
            elif not current_stream.ended:
                # Real contention - stream still active (within 200ms)
                LOGGER.warning(f'Stream contention on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                              f'existing stream (src={int.from_bytes(current_stream.rf_src, "big")}, '
                              f'dst={int.from_bytes(current_stream.dst_id, "big")}, '
                              f'active {time_since_last_packet*1000:.0f}ms ago) '
                              f'vs new stream (src={int.from_bytes(rf_src, "big")}, '
                              f'dst={int.from_bytes(dst_id, "big")})')
                return False
            else:
                # Stream already ended (in hang time) - let _handle_stream_start check hang time rules
                return self._handle_stream_start(repeater, rf_src, dst_id, slot, stream_id, call_type_bit)
        
        # Update stream state
        current_stream.last_seen = time()
        current_stream.packet_count += 1
        
        return True
        
    def _remove_repeater(self, repeater_id: bytes, reason: str) -> None:
        """
        Remove a repeater and clean up all its state.
        This ensures we don't have any memory leaks from lingering references.
        """
        if repeater_id in self._repeaters:
            repeater = self._repeaters[repeater_id]
            
            # Log current state before removal
            LOGGER.debug(f'Removing repeater {self._rid_to_int(repeater_id)}: reason={reason}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
            # Emit event before removing so dashboard can update
            self._events.emit('repeater_disconnected', {
                'repeater_id': self._rid_to_int(repeater_id),
                'callsign': repeater.callsign.decode().strip() if repeater.callsign else 'Unknown',
                'reason': reason
            })
            
            # Remove from active repeaters
            del self._repeaters[repeater_id]
            
            # Clean up the cached repeater_id conversion to prevent memory leak
            # (repeater_ids are bytes objects that won't be GC'd otherwise)
            if repeater_id in self._rid_cache:
                del self._rid_cache[repeater_id]
            

    def _handle_repeater_login(self, repeater_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater login request"""
        # Handle both IPv4 (ip, port) and IPv6 (ip, port, flowinfo, scopeid) address formats
        ip = addr[0]
        port = addr[1]
        
        LOGGER.debug(f'Processing login for repeater ID {self._rid_to_int(repeater_id)} from {ip}:{port}')
        
        # ID Conflict Protection: Check if this ID is reserved for an outbound connection
        # Outbound connections (admin-configured) have priority over inbound repeaters (untrusted)
        repeater_id_int = self._rid_to_int(repeater_id)
        if repeater_id_int in self._outbound_ids:
            LOGGER.warning(f'⛔ Rejecting inbound repeater {repeater_id_int} from {ip}:{port} '
                         f'- ID reserved for outbound connection')
            self._send_nak(repeater_id, addr, reason="ID reserved for outbound connection")
            return
        
        if repeater_id in self._repeaters:
            repeater = self._repeaters[repeater_id]
            if not self._addr_matches(repeater.sockaddr, addr):
                LOGGER.warning(f'Repeater {self._rid_to_int(repeater_id)} attempting to connect from {ip}:{port} but already connected from {repeater.ip}:{repeater.port}')
                # Remove the old registration first
                old_addr = repeater.sockaddr
                self._remove_repeater(repeater_id, "reconnect_different_port")
                # Then send NAK to the old address to ensure cleanup
                self._send_nak(repeater_id, old_addr, reason="Repeater reconnecting from new address")
                # Continue with new connection below
            else:
                # Same repeater reconnecting from same IP:port
                old_state = repeater.connection_state
                LOGGER.info(f'Repeater {self._rid_to_int(repeater_id)} reconnecting while in state {old_state}')
                # Preserve existing salt on login retry
                if old_state == 'login':
                    existing_salt = repeater.salt
                    repeater = RepeaterState(repeater_id=repeater_id, ip=ip, port=port)
                    repeater.salt = existing_salt  # Reuse same salt
                    repeater.connection_state = 'login'
                    self._repeaters[repeater_id] = repeater
                    
                    # Send login ACK with same salt
                    salt_bytes = repeater.salt.to_bytes(4, 'big')
                    self._send_packet(b''.join([RPTACK, salt_bytes]), addr)
                    LOGGER.info(f'Repeater {self._rid_to_int(repeater_id)} login retry from {ip}:{port}, resending same salt: {repeater.salt}')
                    return
                
        # Create or update repeater state (fresh login)
        repeater = RepeaterState(repeater_id=repeater_id, ip=ip, port=port)
        repeater.connection_state = 'login'
        self._repeaters[repeater_id] = repeater
        
        # Send login ACK with salt
        salt_bytes = repeater.salt.to_bytes(4, 'big')
        self._send_packet(b''.join([RPTACK, salt_bytes]), addr)
        LOGGER.info(f'Repeater {self._rid_to_int(repeater_id)} login request from {ip}:{port}, sent salt: {repeater.salt}')

    def _handle_auth_response(self, repeater_id: bytes, auth_hash: bytes, addr: PeerAddress) -> None:
        """Handle authentication response from repeater"""
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater or repeater.connection_state != 'login':
            LOGGER.warning(f'Auth response from repeater {self._rid_to_int(repeater_id)} in wrong state')
            self._send_nak(repeater_id, addr)
            return
            
        try:
            # Get config for this repeater including its passphrase
            repeater_config = self._matcher.get_repeater_config(
                self._rid_to_int(repeater_id),
                repeater.get_callsign_str()
            )
            
            # If no matching configuration found, reject the connection
            if repeater_config is None:
                LOGGER.warning(f'Repeater {self._rid_to_int(repeater_id)} does not match any configured patterns and no default is set')
                self._send_nak(repeater_id, addr, reason="No matching configuration")
                self._remove_repeater(repeater_id, "no_config_match")
                return
            
            # Validate the hash
            salt_bytes = repeater.salt.to_bytes(4, 'big')
            calc_hash = bytes.fromhex(sha256(b''.join([salt_bytes, repeater_config.passphrase.encode()])).hexdigest())
            
            if auth_hash == calc_hash:
                repeater.authenticated = True
                repeater.connection_state = 'config'
                self._send_packet(b''.join([RPTACK, repeater_id]), addr)
                LOGGER.info(f'Repeater {self._rid_to_int(repeater_id)} authenticated successfully')
            else:
                LOGGER.warning(f'Repeater {self._rid_to_int(repeater_id)} failed authentication')
                self._send_nak(repeater_id, addr, reason="Authentication failed")
                self._remove_repeater(repeater_id, "auth_failed")
                
        except Exception as e:
            LOGGER.error(f'Authentication error for repeater {self._rid_to_int(repeater_id)}: {str(e)}')
            self._send_nak(repeater_id, addr)
            self._remove_repeater(repeater_id, "auth_error")

    def _handle_config(self, data: bytes, addr: PeerAddress) -> None:
        """Handle configuration from repeater"""
        try:
            repeater_id = data[4:8]
            repeater = self._validate_repeater(repeater_id, addr)
            if not repeater or not repeater.authenticated or repeater.connection_state != 'config':
                LOGGER.warning(f'Config from repeater {self._rid_to_int(repeater_id)} in wrong state')
                self._send_nak(repeater_id, addr)
                return
                
            # Store raw bytes for metadata
            repeater.callsign = data[8:16]
            repeater.rx_freq = data[16:25]
            repeater.tx_freq = data[25:34]
            repeater.tx_power = data[34:36]
            repeater.colorcode = data[36:38]
            repeater.latitude = data[38:46]
            repeater.longitude = data[46:55]
            repeater.height = data[55:58]
            repeater.location = data[58:78]
            repeater.description = data[78:97]
            repeater.slots = data[97:98]
            repeater.url = data[98:222]
            repeater.software_id = data[222:262]
            repeater.package_id = data[262:302]
            
            # Log detailed configuration at debug level
            LOGGER.debug(f'Repeater {self._rid_to_int(repeater_id)} config:'
                      f'\n    Callsign: {repeater.callsign.decode().strip()}'
                      f'\n    RX Freq: {repeater.rx_freq.decode().strip()}'
                      f'\n    TX Freq: {repeater.tx_freq.decode().strip()}'
                      f'\n    Power: {repeater.tx_power.decode().strip()}'
                      f'\n    ColorCode: {repeater.colorcode.decode().strip()}'
                      f'\n    Location: {repeater.location.decode().strip()}'
                      f'\n    Software: {repeater.software_id.decode().strip()}')

            repeater.connected = True
            repeater.connection_state = 'connected'
            
            # Load and cache TG sets from config for fast routing checks
            self._load_repeater_tg_config(repeater_id, repeater)
            
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)
            LOGGER.info(f'Repeater {self._rid_to_int(repeater_id)} ({repeater.get_callsign_str()}) configured successfully')
            LOGGER.debug(f'Repeater state after config: id={self._rid_to_int(repeater_id)}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
            # Emit detailed repeater info (sent once on connection)
            self._emit_repeater_details(repeater_id, repeater)
            
            # Emit repeater_connected event (lightweight, will be sent on ping updates)
            self._events.emit('repeater_connected', self._prepare_repeater_event_data(repeater_id, repeater))
            
        except Exception as e:
            LOGGER.error(f'Error parsing config: {str(e)}')
            if 'repeater_id' in locals():
                self._send_nak(repeater_id, addr)

    def _emit_repeater_details(self, repeater_id: bytes, repeater: RepeaterState) -> None:
        """
        Emit detailed repeater information (sent once on connection).
        This includes metadata and pattern match information that doesn't change during the connection.
        """
        rid_int = self._rid_to_int(repeater_id)
        callsign = repeater.get_callsign_str()
        
        # Get pattern match info
        try:
            pattern = self._matcher.get_pattern_for_repeater(rid_int, callsign)
            if pattern:
                pattern_name = pattern.name
                pattern_desc = pattern.description if hasattr(pattern, 'description') else ''
                
                # Determine which match type succeeded
                if rid_int in pattern.ids:
                    match_reason = f"specific_id: {rid_int}"
                elif any(start <= rid_int <= end for start, end in pattern.id_ranges):
                    for start, end in pattern.id_ranges:
                        if start <= rid_int <= end:
                            match_reason = f"id_range: {start}-{end}"
                            break
                elif callsign and pattern.callsigns:
                    for pattern_str in pattern.callsigns:
                        pattern_regex = pattern_str.replace('*', '.*') if '*' in pattern_str else pattern_str
                        import re
                        if re.match(f"^{pattern_regex}$", callsign, re.IGNORECASE):
                            match_reason = f"callsign: {pattern_str}"
                            break
                    else:
                        match_reason = "pattern_match"
                else:
                    match_reason = "pattern_match"
            else:
                pattern_name = "Default"
                pattern_desc = "Using default configuration"
                match_reason = "default"
        except Exception as e:
            LOGGER.warning(f'Could not determine pattern for repeater {rid_int}: {e}')
            pattern_name = "Unknown"
            pattern_desc = ""
            match_reason = "unknown"
        
        # Emit detailed info event
        self._events.emit('repeater_details', {
            'repeater_id': rid_int,
            'latitude': repeater.latitude.decode('utf-8', errors='ignore').strip() if repeater.latitude else '',
            'longitude': repeater.longitude.decode('utf-8', errors='ignore').strip() if repeater.longitude else '',
            'height': repeater.height.decode('utf-8', errors='ignore').strip() if repeater.height else '',
            'tx_power': repeater.tx_power.decode('utf-8', errors='ignore').strip() if repeater.tx_power else '',
            'description': repeater.description.decode('utf-8', errors='ignore').strip() if repeater.description else '',
            'url': repeater.url.decode('utf-8', errors='ignore').strip() if repeater.url else '',
            'software_id': repeater.software_id.decode('utf-8', errors='ignore').strip() if repeater.software_id else '',
            'package_id': repeater.package_id.decode('utf-8', errors='ignore').strip() if repeater.package_id else '',
            'slots': repeater.slots.decode('utf-8', errors='ignore').strip() if repeater.slots else '',
            'matched_pattern': pattern_name,
            'pattern_description': pattern_desc,
            'match_reason': match_reason
        })

    def _handle_options(self, repeater_id: bytes, data: bytes, addr: PeerAddress) -> None:
        """
        Handle RPTO message - parse TG options and update repeater's allowed TGs.
        Only TGs that are in the original config are accepted (config has final say).
        
        Format: TS1=tg1,tg2;TS2=tg3,tg4
        """
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater:
            return
        
        try:
            # Parse options string
            options_str = data.decode('utf-8', errors='ignore').strip('\x00').strip()
            LOGGER.info(f'📋 OPTIONS from {self._rid_to_int(repeater_id)} ({repeater.callsign.decode().strip()}): {options_str}')
            
            # Get original config TGs (these are the master allow list)
            repeater_config = self._matcher.get_repeater_config(
                self._rid_to_int(repeater_id),
                repeater.callsign.decode().strip() if repeater.callsign else None
            )
            
            # Convert config to sets, handling None (allow all) properly
            # None = allow all TGs, [] = deny all, [1,2,3] = specific TGs
            config_ts1 = set(repeater_config.slot1_talkgroups) if repeater_config.slot1_talkgroups is not None else None
            config_ts2 = set(repeater_config.slot2_talkgroups) if repeater_config.slot2_talkgroups is not None else None
            
            # Parse RPTO format: "TS1=1,2,3;TS2=4,5,6;..."
            requested_ts1 = set()
            requested_ts2 = set()
            
            for part in options_str.split(';'):
                part = part.strip()
                if not part or '=' not in part:
                    continue
                key, value = part.split('=', 1)
                key = key.strip().upper()
                
                if key == 'TS1' and value:
                    requested_ts1 = {int(tg.strip()) for tg in value.split(',') 
                                     if tg.strip().isdigit()}
                elif key == 'TS2' and value:
                    requested_ts2 = {int(tg.strip()) for tg in value.split(',') 
                                     if tg.strip().isdigit()}
            
            # Check if this repeater is trusted
            if repeater_config.trust:
                # Trusted repeater: use requested TGs as-is, config TGs become defaults
                final_ts1 = requested_ts1 if requested_ts1 else (config_ts1 if config_ts1 else None)
                final_ts2 = requested_ts2 if requested_ts2 else (config_ts2 if config_ts2 else None)
                
                # Log trust usage - show any TGs beyond config (informational, not warning)
                if config_ts1 is not None and requested_ts1:
                    extra_ts1 = requested_ts1 - config_ts1
                    if extra_ts1:
                        LOGGER.info(f'🔓 Trusted repeater {self._rid_to_int(repeater_id)} using additional TS1 TGs: {sorted(extra_ts1)}')
                if config_ts2 is not None and requested_ts2:
                    extra_ts2 = requested_ts2 - config_ts2
                    if extra_ts2:
                        LOGGER.info(f'🔓 Trusted repeater {self._rid_to_int(repeater_id)} using additional TS2 TGs: {sorted(extra_ts2)}')
                
                rejected_ts1 = set()
                rejected_ts2 = set()
            else:
                # Standard behavior: intersection of requested and config
                # If config is None (allow all), any RPTO request is valid (subset of "all")
                # If config is a set, only grant intersection (RPTO can restrict, not expand)
                if config_ts1 is None:
                    # Config allows all TGs, so grant whatever repeater requested
                    final_ts1 = requested_ts1 if requested_ts1 else None  # None = keep "allow all"
                else:
                    # Config has specific TGs, filter RPTO to only those in config
                    final_ts1 = requested_ts1 & config_ts1 if requested_ts1 else config_ts1
                
                if config_ts2 is None:
                    final_ts2 = requested_ts2 if requested_ts2 else None
                else:
                    final_ts2 = requested_ts2 & config_ts2 if requested_ts2 else config_ts2
            
                # Log any requested TGs that were rejected (only when config has restrictions)
                if config_ts1 is not None:
                    rejected_ts1 = requested_ts1 - config_ts1
                else:
                    rejected_ts1 = set()  # No rejections when config allows all
                
                if config_ts2 is not None:
                    rejected_ts2 = requested_ts2 - config_ts2
                else:
                    rejected_ts2 = set()
            
            if rejected_ts1:
                LOGGER.warning(f'⚠️  TS1 TG(s) {sorted(rejected_ts1)} requested by repeater {self._rid_to_int(repeater_id)} not allowed by config')
            if rejected_ts2:
                LOGGER.warning(f'⚠️  TS2 TG(s) {sorted(rejected_ts2)} requested by repeater {self._rid_to_int(repeater_id)} not allowed by config')
            
            # Replace repeater's TG sets (no need to keep old ones)
            repeater.slot1_talkgroups = final_ts1
            repeater.slot2_talkgroups = final_ts2
            repeater.rpto_received = True  # Mark that RPTO was received
            
            # Log final TG lists (handle None = allow all)
            LOGGER.info(f'  → TS1 TGs: {self._format_tg_display(final_ts1)}')
            LOGGER.info(f'  → TS2 TGs: {self._format_tg_display(final_ts2)}')
            
            # Emit event to update dashboard in real-time
            self._events.emit('repeater_options_updated', {
                'repeater_id': self._rid_to_int(repeater_id),
                'slot1_talkgroups': self._format_tg_json(final_ts1),
                'slot2_talkgroups': self._format_tg_json(final_ts2),
                'rpto_received': True
            })
            
            # Send ACK
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)
            
        except Exception as e:
            LOGGER.error(f'Error processing RPTO from {self._rid_to_int(repeater_id)}: {e}')
            # Still send ACK to avoid retries
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)

    def _handle_talker_alias(self, repeater_id: bytes, data: bytes, addr: PeerAddress) -> None:
        """
        Handle DMRA message - Talker Alias information from repeater.
        This provides DMR Talker Alias data blocks (typically callsign/name).
        
        Format is DMR Talker Alias protocol - we acknowledge but don't process yet.
        Future enhancement: parse and display talker alias in dashboard.
        """
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater:
            return
        
        try:
            # Talker alias data is variable length, typically contains:
            # - Header (format, length)
            # - Text blocks (7-bit encoded callsign/name)
            # For now, just acknowledge receipt
            LOGGER.debug(f'📻 Talker Alias from {self._rid_to_int(repeater_id)} ({repeater.get_callsign_str()})')
            
            # TODO: Future enhancement - parse talker alias blocks and emit to dashboard
            # Talker alias format: https://github.com/g4klx/MMDVMHost/wiki/Talker-Alias
            
            # Send ACK to confirm receipt
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)
            
        except Exception as e:
            LOGGER.error(f'Error processing DMRA from {self._rid_to_int(repeater_id)}: {e}')
            # Still send ACK to avoid retries
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)

    def _handle_ping(self, repeater_id: bytes, addr: PeerAddress) -> None:
        """Handle ping (RPTPING/RPTP) from the repeater as a keepalive."""
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater or repeater.connection_state != 'connected':
            LOGGER.warning(f'Ping from repeater {self._rid_to_int(repeater_id)} in wrong state (state="{repeater.connection_state}" if repeater else "None")')
            self._send_nak(repeater_id, addr, reason="Wrong connection state")
            return
            
        # Update ping time and reset missed pings
        repeater.last_ping = time()
        had_missed_pings = repeater.missed_pings > 0
        if had_missed_pings:
            LOGGER.info(f'Ping counter reset for repeater {self._rid_to_int(repeater_id)} after {repeater.missed_pings} missed pings')
        repeater.missed_pings = 0
        repeater.ping_count += 1
        
        # Emit event to update dashboard if we had missed pings (to clear warning)
        if had_missed_pings:
            self._events.emit('repeater_connected', self._prepare_repeater_event_data(repeater_id, repeater))
        
        # Send MSTPONG in response to RPTPING/RPTP from repeater
        LOGGER.debug(f'Sending MSTPONG to repeater {self._rid_to_int(repeater_id)}')
        self._send_packet(b''.join([MSTPONG, repeater_id]), addr)

    def _handle_disconnect(self, repeater_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater disconnect"""
        repeater = self._validate_repeater(repeater_id, addr)
        if repeater:
            LOGGER.info(f'Repeater {self._rid_to_int(repeater_id)} ({repeater.get_callsign_str()}) disconnected')
            self._remove_repeater(repeater_id, "disconnect")
            
    def _handle_status(self, repeater_id: bytes, data: bytes, addr: PeerAddress) -> None:
        """Handle repeater status report (including RSSI)"""
        repeater = self._validate_repeater(repeater_id, addr)
        if repeater:
            LOGGER.debug(f'Status report from repeater {self._rid_to_int(repeater_id)}: {data[8:].hex()}')
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)

    def _is_dmr_terminator(self, data: bytes, frame_type: int) -> bool:
        """
        Determine if a DMR packet is a stream terminator by checking the frame type.
        
        In the Homebrew protocol, terminators are indicated in byte 15 of the packet:
        - Bits 4-5 (_frame_type): Must be 0x2 (HBPF_DATA_SYNC - data sync frame)
        - Bits 0-3 (_dtype_vseq): Must be 0x2 (HBPF_SLT_VTERM - voice terminator)
        
        This is much simpler than ETSI sync pattern extraction, as the Homebrew
        protocol explicitly flags terminator frames in the packet header.
        
        Args:
            data: The full DMRD packet (including 20-byte Homebrew header + 33-byte DMR data)
            frame_type: The frame type extracted from byte 15, bits 4-5
                       (0 = voice, 1 = voice sync, 2 = data sync)
        
        Returns:
            bool: True if this is a terminator frame, False otherwise
        
        Note:
            This enables immediate terminator detection (~60ms latency) instead of
            timeout-based detection (~200ms). HBlink3 uses this same method.
        """
        # Check packet length
        if len(data) < 16:
            return False
            
        # Extract the data type / voice sequence from bits 0-3 of byte 15
        _bits = data[15]
        _dtype_vseq = _bits & 0xF
        
        # Terminator: frame_type == 2 (DATA_SYNC) and dtype_vseq == 2 (SLT_VTERM)
        # Constants: HBPF_DATA_SYNC = 0x2, HBPF_SLT_VTERM = 0x2
        return frame_type == 0x2 and _dtype_vseq == 0x2
    
    def _calculate_stream_targets(self, source_repeater_id: bytes, slot: int, 
                                  dst_id: bytes, stream_id: bytes, rf_src: bytes) -> set:
        """
        Calculate which repeaters AND outbound connections should receive this ENTIRE transmission.
        
        Checks both routing rules AND current slot availability at stream start.
        If a slot is busy now, that target is excluded from THIS transmission,
        but will be reconsidered for the NEXT transmission.
        
        This "calculate once per stream" approach provides:
        - Better UX: No partial transmissions (don't join mid-stream)
        - Better performance: No per-packet routing checks
        - Simpler code: Deterministic routing per transmission
        
        Returns:
            Set of target identifiers:
            - repeater_ids (bytes) for local repeaters
            - ('outbound', name) tuples for outbound connections
        """
        tgid = int.from_bytes(dst_id, 'big')
        target_set = set()
        
        # Calculate local repeater targets
        for target_repeater_id, target_repeater in self._repeaters.items():
            # Skip source repeater
            if target_repeater_id == source_repeater_id:
                continue
            
            # Only forward to connected repeaters
            if target_repeater.connection_state != 'connected':
                continue
            
            # Check outbound routing (TG allowed on this repeater/slot)
            if not self._check_outbound_routing(target_repeater_id, slot, tgid):
                continue
            
            # Check slot availability AT STREAM START (not per-packet!)
            # If busy now, exclude from this transmission entirely
            if self._is_slot_busy(target_repeater_id, slot, stream_id, rf_src, dst_id):
                LOGGER.debug(f'Target repeater {int.from_bytes(target_repeater_id, "big")} '
                           f'TS{slot} busy at stream start, excluded from this transmission')
                continue
            
            # Passed all checks - will receive entire transmission
            target_set.add(target_repeater_id)
        
        # Calculate outbound connection targets
        for conn_name, outbound in self._outbounds.items():
            # Only forward to authenticated connections
            if not outbound.authenticated:
                continue
            
            # Check TG routing (is this TG allowed on this outbound connection?)
            allowed_tgs = outbound.slot1_talkgroups if slot == 1 else outbound.slot2_talkgroups
            
            # None = allow all, empty set = deny all, non-empty set = specific TGs
            if allowed_tgs is not None and (not allowed_tgs or tgid not in allowed_tgs):
                continue
            
            # Check TDMA slot availability - outbound connections are like repeaters
            # Each slot can only carry ONE talkgroup stream at a time (air interface constraint)
            current_stream = outbound.get_slot_stream(slot)
            if current_stream:
                # Same stream continuing
                if current_stream.stream_id == stream_id:
                    pass  # Same stream, ok to continue
                # Different stream - check if in hang time or still active
                elif current_stream.ended:
                    # Stream ended, check hang time (protects TG conversations)
                    hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
                    time_since_end = time() - current_stream.end_time if current_stream.end_time else 0
                    if time_since_end < hang_time:
                        # In hang time - only allow same TG or original user
                        same_tg = (current_stream.dst_id == dst_id)
                        same_user = (current_stream.rf_src == rf_src)
                        if not (same_tg or same_user):
                            LOGGER.debug(f'Outbound {conn_name} TS{slot} in hang time, '
                                       f'excluded from this transmission')
                            continue
                else:
                    # Different active stream - slot is busy
                    LOGGER.debug(f'Outbound {conn_name} TS{slot} busy with different stream, '
                               f'excluded from this transmission')
                    continue
            
            # Passed all checks - will receive entire transmission
            target_set.add(('outbound', conn_name))
        
        return target_set
    
    def _forward_stream(self, data: bytes, source_repeater_id: bytes, slot: int, 
                       rf_src: bytes, dst_id: bytes, stream_id: bytes) -> None:
        """
        Forward DMR stream to target repeaters using cached routing.
        
        Targets are calculated ONCE at stream start. No per-packet checks needed!
        This provides massive performance improvement and better user experience.
        
        Args:
            data: Complete DMRD packet (20-byte HBP header + 33-byte DMR data)
            source_repeater_id: Repeater ID of originating repeater
            slot: Timeslot (1 or 2)
            rf_src: RF source subscriber ID (3 bytes)
            dst_id: Destination TGID (3 bytes)  
            stream_id: Unique stream identifier (4 bytes)
        """
        # Get source repeater's stream (which has the routing cache)
        source_repeater = self._repeaters.get(source_repeater_id)
        if not source_repeater:
            return
        
        source_stream = source_repeater.get_slot_stream(slot)
        if not source_stream or source_stream.stream_id != stream_id:
            # This shouldn't happen, but safety check
            LOGGER.warning(f'Forwarding called but no matching stream found')
            return
        
        # Use cached target list (calculated once on stream start!)
        if not source_stream.routing_cached or source_stream.target_repeaters is None:
            # Safety fallback (shouldn't happen)
            LOGGER.warning(f'Stream routing not cached, recalculating')
            source_stream.target_repeaters = self._calculate_stream_targets(
                source_repeater_id, slot, dst_id, stream_id, rf_src
            )
            source_stream.routing_cached = True
        
        # Check if this is a terminator packet
        _bits = data[15]
        _frame_type = (_bits & 0x30) >> 4
        is_terminator = self._is_dmr_terminator(data, _frame_type)
        
        # Simple loop through cached targets - no per-packet checks!
        for target in source_stream.target_repeaters:
            # Check if target is an outbound connection or local repeater
            if isinstance(target, tuple) and target[0] == 'outbound':
                # Target is an outbound connection (we're acting as a repeater to remote server)
                conn_name = target[1]
                outbound = self._outbounds.get(conn_name)
                if not outbound or not outbound.authenticated:
                    continue  # Connection dropped mid-stream
                
                # Forward packet to outbound server
                outbound.transport.sendto(data)
                LOGGER.debug(f'Forwarded to outbound [{conn_name}]')
                
                # Track assumed stream state on outbound slot (TDMA constraint)
                # We must track what we're transmitting on each timeslot
                self._update_assumed_stream_outbound(outbound, slot, rf_src, dst_id, 
                                                    stream_id, is_terminator,
                                                    int.from_bytes(source_repeater_id, 'big'))
                
            else:
                # Target is a local repeater (bytes)
                target_repeater_id = target
                target_repeater = self._repeaters.get(target_repeater_id)
                if not target_repeater:
                    continue  # Repeater disconnected mid-stream
                
                # Forward packet (no routing or slot checks - already approved!)
                self._send_packet(data, target_repeater.sockaddr)
                
                # Track assumed stream state on target repeater
                self._update_assumed_stream(target_repeater, slot, rf_src, dst_id, 
                                           stream_id, is_terminator, 
                                           int.from_bytes(source_repeater_id, 'big'))
    
    def _handle_dmr_data(self, data: bytes, addr: PeerAddress) -> None:
        """Handle DMR data"""
        if len(data) < 55:
            LOGGER.warning(f'Invalid DMR data packet from {addr[0]}:{addr[1]} - length {len(data)} < 55')
            return
            
        repeater_id = data[11:15]
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater or repeater.connection_state != 'connected':
            LOGGER.warning(f'DMR data from repeater {self._rid_to_int(repeater_id)} in wrong state')
            return
            
        # Extract packet information
        _seq = data[4]
        _rf_src = data[5:8]
        _dst_id = data[8:11]
        _bits = data[15]
        _slot = 2 if (_bits & 0x80) else 1
        _call_type = (_bits & 0x40) >> 6  # Bit 6: 1 = private/unit, 0 = group
        _frame_type = (_bits & 0x30) >> 4  # 0 = voice, 1 = voice sync, 2 = data sync, 3 = unused
        _stream_id = data[16:20]  # Stream ID for tracking unique transmissions
        
        # Check if this is a stream terminator (immediate end detection)
        # Note: _is_dmr_terminator() checks packet header flags for immediate detection
        _is_terminator = self._is_dmr_terminator(data, _frame_type)
        
        # Handle stream tracking
        stream_valid = self._handle_stream_packet(repeater, _rf_src, _dst_id, _slot, _stream_id, _call_type)
        
        if not stream_valid:
            # Stream contention or not allowed - drop packet silently
            LOGGER.debug(f'Dropped packet from repeater {self._rid_to_int(repeater_id)} slot {_slot}: '
                        f'src={int.from_bytes(_rf_src, "big")}, dst={int.from_bytes(_dst_id, "big")}, '
                        f'reason=stream contention or talkgroup not allowed')
            return
        
        # Get the current stream for this slot (after _handle_stream_packet has updated it)
        current_stream = repeater.get_slot_stream(_slot)
        
        # Per-packet logging - only enable for heavy troubleshooting
        #LOGGER.debug(f'DMR data from {self._rid_to_int(repeater_id)} slot {_slot}: '
        #            f'seq={_seq}, src={int.from_bytes(_rf_src, "big")}, '
        #            f'dst={int.from_bytes(_dst_id, "big")}, '
        #            f'stream_id={_stream_id.hex()}, '
        #            f'frame_type={_frame_type}, '
        #            f'terminator={_is_terminator}, '
        #            f'packet_count={current_stream.packet_count if current_stream else 0}, '
        #            f'has_lc={current_stream.lc is not None if current_stream else False}')
        
        # Handle terminator frame for immediate stream end detection
        if _is_terminator and current_stream and not current_stream.ended:
            self._end_stream(current_stream, repeater_id, _slot, time(), 'terminator')
        
        # Emit stream_update every 60 packets (10 superframes = 1 second)
        if current_stream and not current_stream.ended and current_stream.packet_count % 60 == 0:
            self._events.emit('stream_update', {
                'repeater_id': self._rid_to_int(repeater_id),
                'slot': _slot,
                'src_id': int.from_bytes(_rf_src, 'big'),
                'dst_id': int.from_bytes(_dst_id, 'big'),
                'duration': round(time() - current_stream.start_time, 2),
                'packets': current_stream.packet_count,
                'call_type': current_stream.call_type
            })
        
        # Stream end detection: terminator (primary) or timeout (fallback)
        # Hang time prevents slot hijacking during conversations
        
        # Forward DMR data to other connected repeaters
        self._forward_stream(data, repeater_id, _slot, _rf_src, _dst_id, _stream_id)

    def _update_assumed_stream(self, repeater: RepeaterState, slot: int, rf_src: bytes, 
                              dst_id: bytes, stream_id: bytes, is_terminator: bool,
                              source_repeater_id: int) -> None:
        """
        Update or create assumed stream state on a target repeater.
        
        Since we're forwarding to this repeater but not receiving feedback,
        we must assume the stream state based on what we're sending.
        
        Args:
            repeater: Target repeater state
            slot: Timeslot
            rf_src: Source subscriber ID
            dst_id: Destination TGID
            stream_id: Stream identifier
            is_terminator: Whether this packet is a terminator
            source_repeater_id: ID of source repeater (for logging)
        """
        current_stream = repeater.get_slot_stream(slot)
        current_time = time()
        
        if not current_stream or current_stream.stream_id != stream_id:
            # New assumed stream starting
            new_stream = StreamState(
                repeater_id=repeater.repeater_id,
                rf_src=rf_src,
                dst_id=dst_id,
                slot=slot,
                start_time=current_time,
                last_seen=current_time,
                stream_id=stream_id,
                packet_count=1,
                call_type="group",  # Assume group call for forwarded streams
                is_assumed=True  # Mark as assumed stream
            )
            repeater.set_slot_stream(slot, new_stream)
            
            # Log at DEBUG level - TX streams are noisy
            LOGGER.debug(f'TX stream started on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                       f'from repeater {source_repeater_id}, '
                       f'src={int.from_bytes(rf_src, "big")}, '
                       f'dst={int.from_bytes(dst_id, "big")}')
            
            # Emit stream_start event for repeater card display (but marked as assumed)
            # Dashboard will filter these from Recent Events log
            self._events.emit('stream_start', {
                'repeater_id': int.from_bytes(repeater.repeater_id, 'big'),
                'slot': slot,
                'src_id': int.from_bytes(rf_src, 'big'),
                'dst_id': int.from_bytes(dst_id, 'big'),
                'call_type': 'group',
                'is_assumed': True
            })
            
            # Update active calls counter
            self._active_calls += 1
        else:
            # Update existing assumed stream
            current_stream.last_seen = current_time
            current_stream.packet_count += 1
        
        # Handle terminator
        if is_terminator and current_stream:
            self._end_stream(current_stream, repeater.repeater_id, slot, current_time, 'terminator')

    def _update_assumed_stream_outbound(self, outbound: OutboundState, slot: int, rf_src: bytes,
                                       dst_id: bytes, stream_id: bytes, is_terminator: bool,
                                       source_repeater_id: int) -> None:
        """
        Update or create assumed stream state on an outbound connection's TDMA slot.
        
        Since we're acting as a repeater to the remote server and forwarding traffic,
        we must track what we're transmitting on each timeslot (TDMA air interface constraint).
        
        Args:
            outbound: Target outbound connection state
            slot: Timeslot (1 or 2)
            rf_src: Source subscriber ID
            dst_id: Destination TGID
            stream_id: Stream identifier
            is_terminator: Whether this packet is a terminator
            source_repeater_id: ID of source repeater (for logging)
        """
        current_stream = outbound.get_slot_stream(slot)
        current_time = time()
        
        if not current_stream or current_stream.stream_id != stream_id:
            # New assumed stream starting on this outbound timeslot
            # Use a dummy repeater_id for outbound streams (can't use bytes for outbound)
            dummy_id = outbound.config.radio_id.to_bytes(4, 'big')
            
            new_stream = StreamState(
                repeater_id=dummy_id,  # Our ID when acting as repeater
                rf_src=rf_src,
                dst_id=dst_id,
                slot=slot,
                start_time=current_time,
                last_seen=current_time,
                stream_id=stream_id,
                packet_count=1,
                call_type="group",  # Assume group call for forwarded streams
                is_assumed=True  # Mark as assumed stream (TX, not RX)
            )
            outbound.set_slot_stream(slot, new_stream)
            
            # Log at DEBUG level - TX streams to outbounds
            LOGGER.debug(f'TX stream started on outbound [{outbound.config.name}] TS{slot}: '
                       f'from repeater {source_repeater_id}, '
                       f'src={int.from_bytes(rf_src, "big")}, '
                       f'dst={int.from_bytes(dst_id, "big")}')
            
            # Increment active calls counter
            self._active_calls += 1
        else:
            # Update existing assumed stream
            current_stream.last_seen = current_time
            current_stream.packet_count += 1
        
        # Handle terminator - end the stream and start hang time
        if is_terminator and current_stream:
            # For outbound streams, use a synthetic repeater_id for logging
            dummy_id = outbound.config.radio_id.to_bytes(4, 'big')
            self._end_stream(current_stream, dummy_id, slot, current_time, 'terminator')


    def _send_packet(self, data: bytes, addr: tuple):
        """Send packet to specified address"""
        cmd = data[:4]
        #if cmd != DMRD:  # Don't log DMR data packets
        #    LOGGER.debug(f'Sending {cmd.decode()} to {addr[0]}:{addr[1]}')
        # asyncio uses sendto() instead of write(data, addr)
        self.transport.sendto(data, self._normalize_addr(addr))

    def _send_nak(self, repeater_id: bytes, addr: tuple, reason: str = None, is_shutdown: bool = False):
        """Send NAK to specified address
        
        Args:
            repeater_id: The repeater's ID
            addr: The address to send the NAK to
            reason: Why the NAK is being sent
            is_shutdown: Whether this NAK is part of a graceful shutdown
        """
        log_level = logging.DEBUG if is_shutdown else logging.WARNING
        log_msg = f'Sending NAK to {addr[0]}:{addr[1]} for repeater {self._rid_to_int(repeater_id)}'
        if reason:
            log_msg += f' - {reason}'
        
        LOGGER.log(log_level, log_msg)
        self._send_packet(b''.join([MSTNAK, repeater_id]), addr)


def cleanup_old_logs(log_dir: pathlib.Path, max_days: int) -> None:
    """Clean up log files older than max_days based on their date suffix"""
    from datetime import datetime, timedelta
    current_date = datetime.now()
    cutoff_date = current_date - timedelta(days=max_days)
    
    try:
        for log_file in log_dir.glob('hblink.log.*'):
            try:
                # Extract date from filename (expecting format: hblink.log.YYYY-MM-DD)
                date_str = log_file.name.split('.')[-1]
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                
                if file_date < cutoff_date:
                    log_file.unlink()
                    LOGGER.debug(f'Deleted old log file from {date_str}: {log_file}')
            except (OSError, ValueError) as e:
                LOGGER.warning(f'Error processing old log file {log_file}: {e}')
    except Exception as e:
        LOGGER.error(f'Error during log cleanup: {e}')

def setup_logging():
    """Configure logging"""
    logging_config = CONFIG.get('global', {}).get('logging', {})
    
    # Get logging configuration with defaults
    log_file = logging_config.get('file', 'logs/hblink.log')
    file_level = getattr(logging, logging_config.get('file_level', 'DEBUG'))
    console_level = getattr(logging, logging_config.get('console_level', 'INFO'))
    max_days = logging_config.get('retention_days', 30)
    
    log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # Create log directory if it doesn't exist
    log_path = pathlib.Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Clean up old log files
    cleanup_old_logs(log_path.parent, max_days)
    
    # Configure rotating file handler with date-based suffix
    file_handler = logging.handlers.TimedRotatingFileHandler(
        str(log_path),
        when='midnight',
        interval=1,
        backupCount=max_days
    )
    # Set the suffix for rotated files to YYYY-MM-DD
    file_handler.suffix = '%Y-%m-%d'
    # Don't include seconds in date suffix
    file_handler.namer = lambda name: name.replace('.%Y-%m-%d%H%M%S', '.%Y-%m-%d')
    file_handler.setFormatter(log_format)
    file_handler.setLevel(file_level)
    
    # Configure console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    console_handler.setLevel(console_level)
    
    # Add handlers and set level to most verbose of the two
    LOGGER.addHandler(file_handler)
    LOGGER.addHandler(console_handler)
    LOGGER.setLevel(min(file_level, console_level))

def load_config(config_file: str):
    """Load JSON configuration file"""
    try:
        with open(config_file, 'r') as f:
            global CONFIG
            CONFIG = json.load(f)
    except Exception as e:
        LOGGER.error(f'Error loading configuration: {e}')
        sys.exit(1)

def parse_outbound_connections() -> List[OutboundConnectionConfig]:
    """Parse outbound connections from config"""
    outbound_configs = []
    
    raw_outbounds = CONFIG.get('outbound_connections', [])
    if not raw_outbounds:
        LOGGER.info('No outbound connections configured')
        return outbound_configs
    
    for idx, conn_dict in enumerate(raw_outbounds):
        try:
            config = OutboundConnectionConfig(
                enabled=conn_dict.get('enabled', True),
                name=conn_dict['name'],
                address=conn_dict['address'],
                port=conn_dict['port'],
                radio_id=conn_dict['radio_id'],
                password=conn_dict['password'],
                options=conn_dict.get('options', ''),
                callsign=conn_dict.get('callsign', ''),
                rx_frequency=conn_dict.get('rx_frequency', 0),
                tx_frequency=conn_dict.get('tx_frequency', 0),
                power=conn_dict.get('power', 0),
                colorcode=conn_dict.get('colorcode', 1),
                latitude=conn_dict.get('latitude', 0.0),
                longitude=conn_dict.get('longitude', 0.0),
                height=conn_dict.get('height', 0),
                location=conn_dict.get('location', ''),
                description=conn_dict.get('description', ''),
                url=conn_dict.get('url', ''),
                software_id=conn_dict.get('software_id', 'HBlink4'),
                package_id=conn_dict.get('package_id', 'HBlink4 v2.0')
            )
            outbound_configs.append(config)
            LOGGER.info(f'✓ Loaded outbound connection: {config.name} → {config.address}:{config.port}')
        except KeyError as e:
            LOGGER.error(f'✗ Outbound connection #{idx} missing required field: {e}')
            sys.exit(1)
        except ValueError as e:
            LOGGER.error(f'✗ Outbound connection #{idx} validation error: {e}')
            sys.exit(1)
    
    return outbound_configs

async def async_main():
    """Main async entry point"""
    loop = asyncio.get_running_loop()
    
    # Load config values
    bind_ipv4 = CONFIG['global'].get('bind_ipv4', '0.0.0.0')
    bind_ipv6 = CONFIG['global'].get('bind_ipv6', '::')
    port_ipv4 = CONFIG['global'].get('port_ipv4', 62031)
    port_ipv6 = CONFIG['global'].get('port_ipv6', 62031)
    disable_ipv6 = CONFIG['global'].get('disable_ipv6', False)
    
    if disable_ipv6:
        LOGGER.warning('⚠️  IPv6 is globally disabled - only binding to IPv4')
        bind_ipv6 = None
    
    transports = []
    protocols = []
    
    # Create IPv4 endpoint
    if bind_ipv4:
        try:
            protocol_v4 = HBProtocol()
            transport_v4, _ = await loop.create_datagram_endpoint(
                lambda: protocol_v4,
                local_addr=(bind_ipv4, port_ipv4)
            )
            transports.append(transport_v4)
            protocols.append(protocol_v4)
            LOGGER.info(f'✓ HBlink4 listening on {bind_ipv4}:{port_ipv4} (UDP, IPv4)')
        except Exception as e:
            LOGGER.error(f'✗ Failed to bind IPv4 to {bind_ipv4}:{port_ipv4}: {e}')
            if bind_ipv4 == '0.0.0.0':
                # Critical failure on wildcard bind
                sys.exit(1)
    
    # Create IPv6 endpoint
    if bind_ipv6 and not disable_ipv6:
        try:
            protocol_v6 = HBProtocol()
            transport_v6, _ = await loop.create_datagram_endpoint(
                lambda: protocol_v6,
                local_addr=(bind_ipv6, port_ipv6)
            )
            transports.append(transport_v6)
            protocols.append(protocol_v6)
            LOGGER.info(f'✓ HBlink4 listening on [{bind_ipv6}]:{port_ipv6} (UDP, IPv6)')
        except OSError as e:
            error_msg = str(e)
            if 'address already in use' in error_msg.lower() or 'address in use' in error_msg.lower():
                if port_ipv4 == port_ipv6 and bind_ipv4 and bind_ipv6 == '::':
                    LOGGER.warning(f'⚠️  IPv6 bind to [::]:{port_ipv6} failed (port in use by IPv4)')
                    LOGGER.warning(f'⚠️  This is normal on dual-stack systems')
                    LOGGER.warning(f'⚠️  Solutions: 1) Use different ports (port_ipv4: {port_ipv4}, port_ipv6: {port_ipv4+1})')
                    LOGGER.warning(f'⚠️             2) Set disable_ipv6: true to use IPv4-only')
                    LOGGER.warning(f'⚠️             3) Set bind_ipv4: "" to let IPv6 handle both')
                else:
                    LOGGER.error(f'✗ Failed to bind IPv6 to [{bind_ipv6}]:{port_ipv6}: {e}')
            else:
                LOGGER.error(f'✗ Failed to bind IPv6 to [{bind_ipv6}]:{port_ipv6}: {e}')
    
    # Verify we have at least one listener
    if not transports:
        LOGGER.error('Failed to bind to any interface')
        sys.exit(1)
    
    # Parse and validate outbound connections
    outbound_configs = parse_outbound_connections()
    
    # Reserve outbound IDs and initialize connections (all protocols share the state)
    if outbound_configs and protocols:
        # Use first protocol for outbound connection management
        primary_protocol = protocols[0]
        for config in outbound_configs:
            if config.enabled:
                # Reserve the ID to prevent DoS
                if config.radio_id in primary_protocol._outbound_ids:
                    LOGGER.error(f'✗ Duplicate outbound ID {config.radio_id} for "{config.name}"')
                    sys.exit(1)
                primary_protocol._outbound_ids.add(config.radio_id)
                LOGGER.info(f'✓ Reserved ID {config.radio_id} for outbound "{config.name}"')
                
                # Start connection task
                task = asyncio.create_task(
                    primary_protocol._connect_outbound(config, loop),
                    name=f'outbound_{config.name}'
                )
                LOGGER.info(f'✓ Started outbound connection task for "{config.name}"')
    
    # Setup signal handlers (Linux/Unix native asyncio pattern)
    shutdown_event = asyncio.Event()
    
    def handle_shutdown(signum):
        signame = signal.Signals(signum).name
        LOGGER.info(f"Received shutdown signal {signame}")
        # Cleanup all protocols (cleanup() logs "Starting graceful shutdown...")
        for protocol in protocols:
            protocol.cleanup()
        # Signal the event loop to exit
        shutdown_event.set()
    
    loop.add_signal_handler(signal.SIGINT, lambda: handle_shutdown(signal.SIGINT))
    loop.add_signal_handler(signal.SIGTERM, lambda: handle_shutdown(signal.SIGTERM))
    
    # Run until shutdown signal received
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    
    LOGGER.info("Shutdown complete")

def main():
    """Main program entry point"""
    if len(sys.argv) < 2:
        print('Usage: run.py [config/config.json]')
        print('Note: If no config file specified, config/config.json will be used')
        print('      Copy config_sample.json to config.json and edit as needed')
        sys.exit(1)

    load_config(sys.argv[1])
    setup_logging()
    
    # Startup banner
    LOGGER.info('🚀 ═══════════════════════════════════════════════════════════════')
    LOGGER.info('🚀 HBLINK4 STARTING UP')
    LOGGER.info('🚀 ═══════════════════════════════════════════════════════════════')
    
    asyncio.run(async_main())

if __name__ == '__main__':
    main()
