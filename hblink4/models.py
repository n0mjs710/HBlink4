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
    # Whether unit (private) calls traverse this outbound link. When True, we
    # forward local unit calls out over this link (broadcast or cache-hit) and
    # accept unit calls arriving on it. When False, unit calls are dropped at
    # the link boundary. Defaults off because peers may not share our unit
    # call model.
    unit_calls_enabled: bool = False

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
class OpenBridgeConnectionConfig:
    """Configuration for an OpenBridge (OBP) trunk connection.

    OBP is a stream-multiplexed, TGID-transparent trunk: no per-user login, no
    options handshake, no per-repeater metadata. ``talkgroup_slots`` is the one
    table that does triple duty — ownership, fail-closed filter, and timeslot
    assignment. See development/openbridge-hblink3-hblink4-design.md.
    """
    enabled: bool
    name: str
    network_id: int          # our OBP network ID; stamped into egress RptrId when preserve_source_peer is False
    local_address: str       # local bind address
    local_port: int
    target_address: str      # remote OBP peer
    target_port: int
    passphrase: str
    # Canonical TGID (3-byte, big-endian) -> local timeslot (1 or 2).
    # Ownership + fail-closed filter + TS assignment, all in one map.
    talkgroup_slots: Dict[bytes, int] = field(default_factory=dict)
    # Retain the true source peer/repeater ID in the egress RptrId instead of
    # overwriting it with network_id (the Brandmeister-spec behavior). Default
    # True favors transparency with cooperating software; set False for
    # strict-spec peers that require network_id in RptrId.
    preserve_source_peer: bool = True
    description: str = ""

    def __post_init__(self):
        """Validate required fields."""
        if not self.name:
            raise ValueError("OpenBridge connection must have a name")
        if not self.target_address:
            raise ValueError(f"OpenBridge connection '{self.name}' must have a target_address")
        if not self.passphrase:
            raise ValueError(f"OpenBridge connection '{self.name}' must have a passphrase")
        for label, p in (('local_port', self.local_port), ('target_port', self.target_port)):
            if p <= 0 or p > 65535:
                raise ValueError(f"OpenBridge connection '{self.name}' has invalid {label}: {p}")
        if not (0 < self.network_id <= 0xFFFFFFFF):
            raise ValueError(f"OpenBridge connection '{self.name}' has invalid network_id: {self.network_id}")


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

    # Unit-call metadata. `is_unit_call` is True when this stream carries a
    # private (subscriber-to-subscriber) call; `dst_id` holds the target radio
    # ID instead of a TGID in that case. `is_broadcast_unit_call` is True when
    # we couldn't locate the target in the user cache and fanned the call out
    # to every unit-enabled repeater — pruned to one-to-one once the target
    # responds (Phase 3).
    is_unit_call: bool = False
    is_broadcast_unit_call: bool = False

    # DMR Link Control for outbound rewrites under translation.
    #
    # lc_base: the 9-byte LC (3B opts + 3B dst + 3B src) captured once at
    # stream start. Decoded from VHEAD when the first forwarded frame is a
    # voice header (preserves FLCO/FID/service-options); otherwise synthesized
    # from LC_OPT_GROUP_DEFAULT + dst_id + rf_src. Source-local addressing
    # — per-target LCs are built from lc_base[0:3] + out_dst + out_src.
    #
    # lc_cache: per-target encoded forms keyed on the outbound (dst, src)
    # tuple (slot doesn't affect LC contents). Entry is
    #   (h_lc: 196-bit bitarray, t_lc: 196-bit bitarray,
    #    emb_lc: {1..4} → 32-bit bitarray)
    # Lazy-filled on first frame that needs a rewrite for that addressing,
    # so untranslated targets pay nothing and translated targets pay one
    # BPTC encode per (dst, src) for the life of the stream.
    lc_base: Optional[bytes] = None
    lc_cache: Dict[Tuple[bytes, bytes], Any] = field(default_factory=dict)

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
class OpenBridgeState:
    """State for an OpenBridge (OBP) trunk connection.

    Unlike RepeaterState / OutboundState (2-slot TDMA, one stream per slot), OBP
    is stream-multiplexed: many concurrent streams, tracked by stream_id. OBP
    concurrency is NOT gated by timeslots — this dict is the deliberate departure
    from the device model that lets a trunk carry a firehose.
    """
    config: OpenBridgeConnectionConfig
    ip: str                  # resolved target IP
    port: int                # target port
    transport: Optional[asyncio.DatagramTransport] = None
    # Many concurrent streams, keyed by 4-byte stream_id (reaped on timeout).
    streams: Dict[bytes, 'StreamState'] = field(default_factory=dict)

    @property
    def sockaddr(self) -> Tuple[str, int]:
        """Target socket address tuple."""
        return (self.ip, self.port)

    def get_stream(self, stream_id: bytes) -> Optional['StreamState']:
        """Return the active stream with this id, or None."""
        return self.streams.get(stream_id)

    def add_stream(self, stream: 'StreamState') -> None:
        """Track a new stream by its stream_id."""
        self.streams[stream.stream_id] = stream

    def remove_stream(self, stream_id: bytes) -> None:
        """Stop tracking a stream (ended or reaped)."""
        self.streams.pop(stream_id, None)

    def owns_tgid(self, dst_id: bytes) -> bool:
        """True if this OBP carries (owns) the given canonical TGID."""
        return dst_id in self.config.talkgroup_slots

    def ts_for_tgid(self, dst_id: bytes) -> Optional[int]:
        """Local timeslot for a canonical TGID on ingress, or None if unmapped (dropped)."""
        return self.config.talkgroup_slots.get(dst_id)


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

    # Whether this repeater participates in unit (private) call routing. Seeded
    # from the matched pattern's `default_unit_calls` when the repeater connects,
    # and overridden by a `UNIT=true|false` entry in RPTO if present.
    unit_calls_enabled: bool = False

    # DMRD translation maps (inverses of each other; empty = no translation).
    # inbound_map:  local (slot,tgid) → network (slot,tgid) — applied when this
    #               repeater SENDS us traffic, converting its local addressing
    #               to network addressing so downstream ACL/routing speaks one
    #               vocabulary.
    # outbound_map: network (slot,tgid) → local (slot,tgid) — applied when we
    #               SEND traffic to this repeater, rewriting into its local
    #               addressing.
    # Key/value: (slot_int_1_or_2, 3-byte tgid). Only populated for trusted
    # repeaters that declared remap rules via RPTO.
    inbound_map: Dict[Tuple[int, bytes], Tuple[int, bytes]] = field(default_factory=dict)
    outbound_map: Dict[Tuple[int, bytes], Tuple[int, bytes]] = field(default_factory=dict)

    # Outbound rf_src override: if set, every group-voice packet forwarded FROM
    # this repeater has its rf_src (bytes 5-7) rewritten to this 3-byte value
    # before going out to other local repeaters or outbound servers. One-way —
    # the rest of the network sees all traffic from this repeater as originating
    # from a single radio ID. None = no rewrite (default).
    tx_src_override: Optional[bytes] = None
    
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