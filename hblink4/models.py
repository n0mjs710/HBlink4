"""
Data models and state classes for HBlink4

This module contains all the data classes used to represent the state
of repeaters, outbound connections, and active streams.

TERMINOLOGY NOTE:
In the codebase, "repeater" refers to ANY inbound connection using the
HomeBrew protocol - this includes actual repeaters, hotspots, and network
links from other servers. The dashboard uses connection_type detection
(see utils.detect_connection_type) to categorize and display these as:
  - Repeaters (full duplex sites)
  - Hotspots (personal devices)
  - Network Inbound (server-to-server links)
  - Other (unrecognized)

Outbound connections (Network Outbound) are tracked separately in OutboundState.
"""
import asyncio
from dataclasses import dataclass, field
from time import time
from random import randint
from typing import Optional, Tuple, Dict, Any

# Import utils functions that these models depend on
try:
    from .utils import safe_decode_bytes, PeerAddress
except ImportError:
    # Fallback for when called from outside package
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils import safe_decode_bytes, PeerAddress


@dataclass
class OutboundConnectionConfig:
    """Configuration for an outbound connection to another server"""
    enabled: bool
    name: str
    address: str
    port: int
    radio_id: int
    passphrase: str
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
        if not self.passphrase:
            raise ValueError(f"Outbound connection '{self.name}' must have a passphrase")
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
    auth_sent: bool = False  # RPTK sent (waiting for auth ACK)
    config_sent: bool = False  # RPTC sent and acked
    options_sent: bool = False  # RPTO sent
    last_ping: float = 0.0  # Last RPTPING sent
    last_pong: float = 0.0  # Last MSTPONG received
    missed_pongs: int = 0  # Consecutive missed pongs
    salt: int = 0  # Challenge salt from MSTCL
    connection_task: Optional[asyncio.Task] = None  # Connection management task
    transport: Optional[asyncio.DatagramTransport] = None  # UDP transport
    
    # Talkgroup filtering (stored as bytes sets for hot path performance)
    # None = no restrictions (allow all), empty set = deny all
    # Format: Set of 3-byte TGIDs (e.g., {b'\x00\x00\x01', b'\x00\x00\x02'})
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
        # Import CONFIG here to avoid circular imports
        try:
            from .hblink import CONFIG
        except ImportError:
            from hblink import CONFIG
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
    """
    Data class for storing inbound connection state.
    
    NOTE: Despite the name, this represents ANY inbound HomeBrew connection,
    not just physical repeaters. The connection_type field is used by the
    dashboard to categorize as: repeater, hotspot, network, or unknown.
    """
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
    
    # Connection type detected from software_id
    # Values: 'repeater', 'hotspot', 'network', 'unknown'
    connection_type: str = 'unknown'
    
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
    
    # Talkgroup access control (stored as bytes sets for hot path performance)
    # None = no restrictions (allow all), empty set = deny all, non-empty set = allow only those TGs
    # Format: Set of 3-byte TGIDs (e.g., {b'\x00\x00\x01', b'\x00\x00\x02'})
    slot1_talkgroups: Optional[set] = None  # Set of 3-byte TGIDs 
    slot2_talkgroups: Optional[set] = None  # Set of 3-byte TGIDs
    
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
            self._callsign_str = safe_decode_bytes(self.callsign)
        return self._callsign_str or 'UNKNOWN'
    
    def get_location_str(self) -> str:
        """Get decoded location string (cached)"""
        if not self._location_str and self.location:
            self._location_str = safe_decode_bytes(self.location)
        return self._location_str or 'Unknown'
    
    def get_rx_freq_str(self) -> str:
        """Get decoded RX frequency string (cached)"""
        if not self._rx_freq_str and self.rx_freq:
            self._rx_freq_str = safe_decode_bytes(self.rx_freq)
        return self._rx_freq_str
    
    def get_tx_freq_str(self) -> str:
        """Get decoded TX frequency string (cached)"""
        if not self._tx_freq_str and self.tx_freq:
            self._tx_freq_str = safe_decode_bytes(self.tx_freq)
        return self._tx_freq_str
    
    def get_colorcode_str(self) -> str:
        """Get decoded color code string (cached)"""
        if not self._colorcode_str and self.colorcode:
            self._colorcode_str = safe_decode_bytes(self.colorcode)
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