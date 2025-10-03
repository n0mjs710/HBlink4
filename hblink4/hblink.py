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
from typing import Dict, Any, Optional, Tuple, Union, List
from time import time
from datetime import date
from random import randint
from hashlib import sha256

import signal
from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol
from twisted.internet.task import LoopingCall

# Global configuration dictionary
CONFIG: Dict[str, Any] = {}
LOGGER = logging.getLogger(__name__)

import os
import sys

# DMR protocol utilities from mature dmr_utils3 library
from dmr_utils3 import decode, bptc

# Try package-relative imports first, fall back to direct imports
try:
    from .constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, MSTCL,
        DMRD, MSTNAK, MSTPONG, RPTPING, RPTACK, RPTP
    )
    from .access_control import RepeaterMatcher
    from .events import EventEmitter
    from .user_cache import UserCache
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, MSTCL,
        DMRD, MSTNAK, MSTPONG, RPTPING, RPTACK, RPTP
    )
    from access_control import RepeaterMatcher
    from events import EventEmitter
    from user_cache import UserCache
    from events import EventEmitter

# Type definitions
PeerAddress = Tuple[str, int]

def bhex(data: bytes) -> bytes:
    """Convert hex bytes to bytes, useful for consistent hex handling"""
    return bytes.fromhex(data.decode())

from dataclasses import dataclass, field

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
    
    # Active stream tracking per slot
    slot1_stream: Optional[StreamState] = None
    slot2_stream: Optional[StreamState] = None
    
    @property
    def sockaddr(self) -> PeerAddress:
        """Get socket address tuple"""
        return (self.ip, self.port)
    
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

class HBProtocol(DatagramProtocol):
    """UDP Implementation of HomeBrew DMR Server Protocol"""
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._repeaters: Dict[bytes, RepeaterState] = {}
        self._config = CONFIG
        self._matcher = RepeaterMatcher(CONFIG)
        self._timeout_task = None
        self._stream_timeout_task = None
        self._user_cache_cleanup_task = None
        self._user_cache_send_task = None
        self._forwarding_stats_send_task = None
        self._port = None  # Store the port instance instead of transport
        self._events = EventEmitter()  # Dashboard event emitter
        
        # Forwarding statistics
        self._forwarding_stats = {
            'active_calls': 0,        # Currently active forwarded calls
            'total_calls_today': 0,   # Total calls forwarded today
            'start_time': time(),
            'last_reset_date': date.today().isoformat()  # Track when stats were last reset
        }
        
        # Track denied streams to avoid repeated logging
        # Key: (repeater_id, slot, stream_id), Value: timestamp of first denial
        self._denied_streams: Dict[tuple, float] = {}
        
        # Initialize user cache if enabled
        user_cache_config = CONFIG.get('global', {}).get('user_cache', {})
        if user_cache_config.get('enabled', True):
            cache_timeout = user_cache_config.get('timeout', 600)
            self._user_cache = UserCache(timeout_seconds=cache_timeout)
            LOGGER.info(f'User cache enabled with {cache_timeout}s timeout')
        else:
            self._user_cache = None
            LOGGER.info('User cache disabled')
        
    def cleanup(self) -> None:
        """Send disconnect messages to all repeaters and cleanup resources."""
        LOGGER.info("Starting graceful shutdown...")
        
        # Send MSTCL to all connected repeaters
        if self._port:  # Only attempt to send if we have a port
            for repeater_id, repeater in self._repeaters.items():
                if repeater.connection_state == 'yes':
                    try:
                        LOGGER.info(f"Sending disconnect to repeater {int.from_bytes(repeater_id, 'big')}")
                        self._port.write(MSTCL, repeater.sockaddr)
                    except Exception as e:
                        LOGGER.error(f"Error sending disconnect to repeater {int.from_bytes(repeater_id, 'big')}: {e}")

        # Give time for disconnects to be sent
        import time
        time.sleep(0.5)  # 500ms should be enough for UDP packets to be sent

    def startProtocol(self):
        """Called when the protocol starts"""
        # Get the port instance for sending data
        self._port = self.transport
        """Called when transport is connected"""
        # Start timeout checker
        timeout_interval = CONFIG.get('timeout', {}).get('repeater', 30)
        self._timeout_task = LoopingCall(self._check_repeater_timeouts)
        self._timeout_task.start(timeout_interval)
        
        # Start stream timeout checker (check more frequently than repeater timeout)
        self._stream_timeout_task = LoopingCall(self._check_stream_timeouts)
        self._stream_timeout_task.start(1.0)  # Check every second
        
        # Start user cache cleanup if enabled
        if self._user_cache:
            cleanup_interval = CONFIG.get('global', {}).get('user_cache', {}).get('cleanup_interval', 60)
            self._user_cache_cleanup_task = LoopingCall(self._cleanup_user_cache)
            self._user_cache_cleanup_task.start(cleanup_interval)
            LOGGER.info(f'User cache cleanup task started (every {cleanup_interval}s)')
            
            # Send user cache to dashboard every 10 seconds
            self._user_cache_send_task = LoopingCall(self._send_user_cache)
            self._user_cache_send_task.start(10.0)
            LOGGER.info('User cache send task started (every 10s)')
        
        # Send forwarding stats to dashboard every 5 seconds
        self._forwarding_stats_send_task = LoopingCall(self._send_forwarding_stats)
        self._forwarding_stats_send_task.start(5.0)
        LOGGER.info('Forwarding stats send task started (every 5s)')
        
        # Check for daily stats reset every minute
        self._daily_reset_task = LoopingCall(self._reset_daily_stats)
        self._daily_reset_task.start(60.0)
        LOGGER.info('Daily stats reset task started (checks every 60s)')

    def stopProtocol(self):
        """Called when transport is disconnected"""
        if self._timeout_task and self._timeout_task.running:
            self._timeout_task.stop()
        if self._stream_timeout_task and self._stream_timeout_task.running:
            self._stream_timeout_task.stop()
        if self._user_cache_cleanup_task and self._user_cache_cleanup_task.running:
            self._user_cache_cleanup_task.stop()
        if self._user_cache_send_task and self._user_cache_send_task.running:
            self._user_cache_send_task.stop()
        if self._forwarding_stats_send_task and self._forwarding_stats_send_task.running:
            self._forwarding_stats_send_task.stop()
        if self._daily_reset_task and self._daily_reset_task.running:
            self._daily_reset_task.stop()
            
    def _check_repeater_timeouts(self):
        """Check for and handle repeater timeouts. Repeaters should send periodic RPTPING/RPTP."""
        current_time = time()
        timeout_duration = CONFIG.get('timeout', {}).get('repeater', 30)  # 30 second default
        max_missed = CONFIG.get('timeout', {}).get('max_missed', 3)  # 3 missed pings default
        
        # Make a list to avoid modifying dict during iteration
        for repeater_id, repeater in list(self._repeaters.items()):
            if repeater.connection_state != 'connected':
                continue
                
            time_since_ping = current_time - repeater.last_ping
            
            if time_since_ping > timeout_duration:
                repeater.missed_pings += 1
                LOGGER.warning(f'Repeater {int.from_bytes(repeater_id, "big")} missed ping #{repeater.missed_pings}')
                
                if repeater.missed_pings >= max_missed:
                    LOGGER.error(f'Repeater {int.from_bytes(repeater_id, "big")} timed out after {repeater.missed_pings} missed pings')
                    # Send NAK to trigger re-registration
                    self._send_nak(repeater_id, (repeater.ip, repeater.port), reason=f"Timeout after {repeater.missed_pings} missed pings")
                    self._remove_repeater(repeater_id, "timeout")
    
    def _check_stream_timeouts(self):
        """Check for and clean up stale streams on all repeaters"""
        current_time = time()
        stream_timeout = CONFIG.get('global', {}).get('stream_timeout', 2.0)
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 3.0)
        
        for repeater_id, repeater in self._repeaters.items():
            if repeater.connection_state != 'connected':
                continue
            
            # Check slot 1
            if repeater.slot1_stream:
                stream = repeater.slot1_stream
                if not stream.is_active(stream_timeout):
                    if not stream.ended:
                        # Stream just ended - mark it and start hang time
                        stream.ended = True
                        stream.end_time = current_time
                        duration = current_time - stream.start_time
                        stream_type = "TX" if stream.is_assumed else "RX"
                        LOGGER.info(f'{stream_type} stream ended on repeater {int.from_bytes(repeater_id, "big")} slot 1: '
                                   f'src={int.from_bytes(stream.rf_src, "big")}, '
                                   f'dst={int.from_bytes(stream.dst_id, "big")}, '
                                   f'duration={duration:.2f}s, '
                                   f'packets={stream.packet_count} - '
                                   f'entering hang time ({hang_time}s)')
                        # Emit stream_end event (includes hang_time since they happen together)
                        self._events.emit('stream_end', {
                            'repeater_id': int.from_bytes(repeater_id, 'big'),
                            'slot': 1,
                            'src_id': int.from_bytes(stream.rf_src, 'big'),
                            'dst_id': int.from_bytes(stream.dst_id, 'big'),
                            'duration': round(duration, 2),
                            'packets': stream.packet_count,
                            'end_reason': 'timeout',
                            'hang_time': hang_time,
                            'call_type': stream.call_type
                        })
                        
                        # Decrement forwarding stats if this was an assumed stream
                        if stream.is_assumed:
                            self._forwarding_stats['active_calls'] -= 1
                    elif not stream.is_in_hang_time(stream_timeout, hang_time):
                        # Hang time expired - clear the slot
                        hang_duration = current_time - stream.end_time if stream.end_time else 0
                        stream_type = "TX" if stream.is_assumed else "RX"
                        LOGGER.info(f'{stream_type} hang time completed on repeater {int.from_bytes(repeater_id, "big")} slot 1: '
                                   f'src={int.from_bytes(stream.rf_src, "big")}, '
                                   f'dst={int.from_bytes(stream.dst_id, "big")}, '
                                   f'hang_duration={hang_duration:.2f}s')
                        # Emit hang_time_expired event so dashboard clears the slot
                        self._events.emit('hang_time_expired', {
                            'repeater_id': int.from_bytes(repeater_id, 'big'),
                            'slot': 1
                        })
                        repeater.slot1_stream = None
            
            # Check slot 2
            if repeater.slot2_stream:
                stream = repeater.slot2_stream
                if not stream.is_active(stream_timeout):
                    if not stream.ended:
                        # Stream just ended - mark it and start hang time
                        stream.ended = True
                        stream.end_time = current_time
                        duration = current_time - stream.start_time
                        stream_type = "TX" if stream.is_assumed else "RX"
                        LOGGER.info(f'{stream_type} stream ended on repeater {int.from_bytes(repeater_id, "big")} slot 2: '
                                   f'src={int.from_bytes(stream.rf_src, "big")}, '
                                   f'dst={int.from_bytes(stream.dst_id, "big")}, '
                                   f'duration={duration:.2f}s, '
                                   f'packets={stream.packet_count} - '
                                   f'entering hang time ({hang_time}s)')
                        # Emit stream_end event (includes hang_time since they happen together)
                        self._events.emit('stream_end', {
                            'repeater_id': int.from_bytes(repeater_id, 'big'),
                            'slot': 2,
                            'src_id': int.from_bytes(stream.rf_src, 'big'),
                            'dst_id': int.from_bytes(stream.dst_id, 'big'),
                            'duration': round(duration, 2),
                            'packets': stream.packet_count,
                            'end_reason': 'timeout',
                            'hang_time': hang_time,
                            'call_type': stream.call_type
                        })
                        
                        # Decrement forwarding stats if this was an assumed stream
                        if stream.is_assumed:
                            self._forwarding_stats['active_calls'] -= 1
                    elif not stream.is_in_hang_time(stream_timeout, hang_time):
                        # Hang time expired - clear the slot
                        hang_duration = current_time - stream.end_time if stream.end_time else 0
                        stream_type = "TX" if stream.is_assumed else "RX"
                        LOGGER.info(f'{stream_type} hang time completed on repeater {int.from_bytes(repeater_id, "big")} slot 2: '
                                   f'src={int.from_bytes(stream.rf_src, "big")}, '
                                   f'dst={int.from_bytes(stream.dst_id, "big")}, '
                                   f'hang_duration={hang_duration:.2f}s')
                        # Emit hang_time_expired event so dashboard clears the slot
                        self._events.emit('hang_time_expired', {
                            'repeater_id': int.from_bytes(repeater_id, 'big'),
                            'slot': 2
                        })
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
    
    def _send_user_cache(self):
        """Send user cache data to dashboard"""
        if self._user_cache:
            last_heard = self._user_cache.get_last_heard(limit=50)  # Limit to 50 for efficiency
            stats = self._user_cache.get_stats()
            
            self._events.emit('last_heard_update', {
                'users': last_heard,
                'stats': stats
            })
    
    def _send_forwarding_stats(self):
        """Send forwarding statistics to dashboard"""
        self._events.emit('forwarding_stats', {
            'active_calls': self._forwarding_stats['active_calls'],
            'total_calls_today': self._forwarding_stats['total_calls_today'],
            'uptime_seconds': time() - self._forwarding_stats['start_time']
        })
    
    def _reset_daily_stats(self):
        """Reset daily statistics at midnight"""
        current_date = date.today().isoformat()
        if current_date != self._forwarding_stats.get('last_reset_date'):
            self._forwarding_stats['total_calls_today'] = 0
            self._forwarding_stats['last_reset_date'] = current_date
            LOGGER.info(f'📊 Daily forwarding stats reset at midnight (server time)')
    
    def _check_inbound_routing(self, repeater_id: bytes, slot: int, tgid: int) -> bool:
        """
        Check if a repeater is allowed to send traffic on this TS/TGID.
        
        Uses slot1_talkgroups and slot2_talkgroups lists from config.
        If not configured, all traffic is allowed (backward compatibility).
        
        Args:
            repeater_id: Repeater ID to check
            slot: Timeslot (1 or 2)
            tgid: Talkgroup ID
            
        Returns:
            True if traffic is allowed, False otherwise
        """
        # Get repeater configuration
        repeater_id_int = int.from_bytes(repeater_id, 'big')
        config = self._matcher.get_repeater_config(repeater_id_int)
        
        # Get slot-specific talkgroup list
        if slot == 1:
            allowed_tgids = config.slot1_talkgroups
        else:
            allowed_tgids = config.slot2_talkgroups
        
        # Empty list means accept all (backward compatibility)
        if not allowed_tgids:
            return True
        
        # Check if TGID is in the allowed list for this slot
        return tgid in allowed_tgids
    
    def _check_outbound_routing(self, repeater_id: bytes, slot: int, tgid: int) -> bool:
        """
        Check if traffic should be forwarded to this repeater on this TS/TGID.
        
        Uses slot1_talkgroups and slot2_talkgroups lists from config.
        Same list is used for both inbound and outbound (simplicity).
        
        Args:
            repeater_id: Repeater ID to check
            slot: Timeslot (1 or 2)  
            tgid: Talkgroup ID
            
        Returns:
            True if traffic should be forwarded, False otherwise
        """
        # Get repeater configuration
        repeater_id_int = int.from_bytes(repeater_id, 'big')
        config = self._matcher.get_repeater_config(repeater_id_int)
        
        # Get slot-specific talkgroup list
        if slot == 1:
            allowed_tgids = config.slot1_talkgroups
        else:
            allowed_tgids = config.slot2_talkgroups
        
        # Empty list means send nothing (safe default)
        if not allowed_tgids:
            return False
        
        # Check if TGID is in the allowed list for this slot
        return tgid in allowed_tgids
    
    def _is_slot_busy(self, repeater_id: bytes, slot: int, stream_id: bytes) -> bool:
        """
        Check if a slot is busy with a different stream (contention check).
        
        Args:
            repeater_id: Repeater ID to check
            slot: Timeslot to check
            stream_id: Current stream ID (to allow same stream through)
            
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
        
        # Check if stream has ended and is outside hang time
        current_time = time()
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
        
        if current_stream.end_time:
            # Stream has ended, check hang time
            time_since_end = current_time - current_stream.end_time
            if time_since_end > hang_time:
                return False  # Hang time expired, slot is free
        
        # Slot is busy with a different active stream or in hang time
        return True
    
    def get_user_cache_data(self, limit: int = 50) -> List[dict]:
        """
        Get last heard users from cache.
        
        Args:
            limit: Maximum number of entries to return
            
        Returns:
            List of user entries sorted by most recent first
        """
        if self._user_cache:
            return self._user_cache.get_last_heard(limit)
        return []

    def datagramReceived(self, data: bytes, addr: tuple):
        """Handle received UDP datagram"""
        ip, port = addr
        
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
            elif _command == RPTC:
                if data[:5] == RPTCL:
                    repeater_id = data[5:9]
                else:
                    repeater_id = data[4:8]
                
            if repeater_id:
                # Per-packet logging - only enable for heavy troubleshooting
                #LOGGER.debug(f'Packet received: cmd={_command}, repeater_id={int.from_bytes(repeater_id, "big")}, addr={addr}')
                pass
            else:
                LOGGER.warning(f'Packet received with unknown command: cmd={_command}, repeater_id={int.from_bytes(repeater_id, "big")}, addr={addr}')   
                return
            
            # Update ping time for connected repeaters
            if repeater_id and repeater_id in self._repeaters:
                repeater = self._repeaters[repeater_id]
                if repeater.connection_state == 'connected':
                    repeater.last_ping = time()
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
            else:
                LOGGER.warning(f'Unknown command received from {ip}:{port}: {_command}')
        except Exception as e:
            LOGGER.error(f'Error processing datagram from {ip}:{port}: {str(e)}')

    def _validate_repeater(self, repeater_id: bytes, addr: PeerAddress) -> Optional[RepeaterState]:
        """Validate repeater state and address"""
        if repeater_id not in self._repeaters:
            # Per-packet logging - only enable for heavy troubleshooting
            #LOGGER.debug(f'Repeater {int.from_bytes(repeater_id, "big")} not found in _repeaters dict')
            self._send_nak(repeater_id, addr, reason="Repeater not registered")
            return None
            
        repeater = self._repeaters[repeater_id]
        # Per-packet logging - only enable for heavy troubleshooting
        #LOGGER.debug(f'Validating repeater {int.from_bytes(repeater_id, "big")}: state="{repeater.connection_state}", stored_addr={repeater.sockaddr}, incoming_addr={addr}')
        
        if repeater.sockaddr != addr:
            LOGGER.warning(f'Message from wrong IP for repeater {int.from_bytes(repeater_id, "big")}')
            self._send_nak(repeater_id, addr, reason="Message from incorrect IP address")
            return None
            
        return repeater
    
    def _is_talkgroup_allowed(self, repeater: RepeaterState, dst_id: bytes) -> bool:
        """Check if a talkgroup is allowed for this repeater based on its configuration"""
        try:
            # Get the repeater's configuration
            repeater_config = self._matcher.get_repeater_config(
                int.from_bytes(repeater.repeater_id, 'big'),
                repeater.callsign.decode().strip() if repeater.callsign else None
            )
            
            # Convert dst_id to int for comparison
            talkgroup = int.from_bytes(dst_id, 'big')
            
            # Check if this talkgroup is in the allowed list
            return talkgroup in repeater_config.talkgroups
            
        except Exception as e:
            LOGGER.error(f'Error checking talkgroup permissions: {e}')
            return False
    
    def _handle_stream_start(self, repeater: RepeaterState, rf_src: bytes, dst_id: bytes, 
                             slot: int, stream_id: bytes, call_type_bit: int = 1) -> bool:
        """
        Handle the start of a new stream on a repeater slot.
        Returns True if the stream can proceed, False if there's a contention.
        """
        current_stream = repeater.get_slot_stream(slot)
        current_time = time()
        
        # Check if there's already an active stream on this slot
        if current_stream:
            # Same stream continuing (same stream_id)
            if current_stream.stream_id == stream_id:
                return True
            
            # Check if stream is in hang time
            if current_stream.ended:
                # Stream has ended but is in hang time
                # Allow same RF source to continue, deny different source
                if current_stream.rf_src == rf_src:
                    LOGGER.info(f'Same source resuming on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot} '
                               f'during hang time: src={int.from_bytes(rf_src, "big")}, '
                               f'old_dst={int.from_bytes(current_stream.dst_id, "big")}, '
                               f'new_dst={int.from_bytes(dst_id, "big")}')
                    # Allow by falling through to create new stream
                else:
                    LOGGER.warning(f'Hang time contention on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                                  f'slot reserved for src={int.from_bytes(current_stream.rf_src, "big")}, '
                                  f'denied src={int.from_bytes(rf_src, "big")}')
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
                # Get repeater config to show allowed list in log
                repeater_id_int = int.from_bytes(repeater.repeater_id, 'big')
                config = self._matcher.get_repeater_config(repeater_id_int)
                allowed_tgids = config.slot1_talkgroups if slot == 1 else config.slot2_talkgroups
                LOGGER.warning(f'Inbound routing denied: repeater={repeater_id_int} '
                              f'TS{slot}/TG{tgid} not in allowed list {allowed_tgids}')
                
                # Add to denied cache
                self._denied_streams[denial_key] = current_time
            
            return False
        
        # No active stream, start a new one
        new_stream = StreamState(
            repeater_id=repeater.repeater_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            start_time=current_time,
            last_seen=current_time,
            stream_id=stream_id,
            packet_count=1,
            call_type="private" if call_type_bit else "group"  # Set from packet header bit (1=private, 0=group)
        )
        
        repeater.set_slot_stream(slot, new_stream)
        
        LOGGER.info(f'RX stream started on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                   f'src={int.from_bytes(rf_src, "big")}, dst={int.from_bytes(dst_id, "big")}, '
                   f'stream_id={stream_id.hex()}')
        
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
            
            if time_since_last_packet > 0.2:  # 200ms threshold
                # Old stream appears terminated - force end it and allow new stream
                duration = current_time - current_stream.start_time
                LOGGER.info(f'Fast terminator: stream on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot} '
                           f'ended via inactivity ({time_since_last_packet*1000:.0f}ms since last packet): '
                           f'src={int.from_bytes(current_stream.rf_src, "big")}, '
                           f'dst={int.from_bytes(current_stream.dst_id, "big")}, '
                           f'duration={duration:.2f}s, packets={current_stream.packet_count}')
                
                # Emit stream_end event
                self._events.emit('stream_end', {
                    'repeater_id': int.from_bytes(repeater.repeater_id, 'big'),
                    'slot': slot,
                    'src_id': int.from_bytes(current_stream.rf_src, 'big'),
                    'dst_id': int.from_bytes(current_stream.dst_id, 'big'),
                    'duration': round(duration, 2),
                    'packets': current_stream.packet_count,
                    'call_type': current_stream.call_type,
                    'end_reason': 'fast_terminator'
                })
                
                # Clear the old stream and start new one
                repeater.set_slot_stream(slot, None)
                return self._handle_stream_start(repeater, rf_src, dst_id, slot, stream_id, call_type_bit)
            else:
                # Real contention - stream still active
                LOGGER.warning(f'Stream contention on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                              f'existing stream (src={int.from_bytes(current_stream.rf_src, "big")}, '
                              f'dst={int.from_bytes(current_stream.dst_id, "big")}, '
                              f'active {time_since_last_packet*1000:.0f}ms ago) '
                              f'vs new stream (src={int.from_bytes(rf_src, "big")}, '
                              f'dst={int.from_bytes(dst_id, "big")})')
                return False
        
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
            LOGGER.debug(f'Removing repeater {int.from_bytes(repeater_id, "big")}: reason={reason}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
            # Remove from active repeaters
            del self._repeaters[repeater_id]
            

    def _handle_repeater_login(self, repeater_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater login request"""
        ip, port = addr
        
        LOGGER.debug(f'Processing login for repeater ID {int.from_bytes(repeater_id, "big")} from {ip}:{port}')
        
        if repeater_id in self._repeaters:
            repeater = self._repeaters[repeater_id]
            if repeater.sockaddr != addr:
                LOGGER.warning(f'Repeater {int.from_bytes(repeater_id, "big")} attempting to connect from {ip}:{port} but already connected from {repeater.ip}:{repeater.port}')
                # Remove the old registration first
                old_addr = repeater.sockaddr
                self._remove_repeater(repeater_id, "reconnect_different_port")
                # Then send NAK to the old address to ensure cleanup
                self._send_nak(repeater_id, old_addr, reason="Repeater reconnecting from new address")
                # Continue with new connection below
            else:
                # Same repeater reconnecting from same IP:port
                old_state = repeater.connection_state
                LOGGER.info(f'Repeater {int.from_bytes(repeater_id, "big")} reconnecting while in state {old_state}')
                
        # Create or update repeater state
        repeater = RepeaterState(repeater_id=repeater_id, ip=ip, port=port)
        repeater.connection_state = 'login'
        self._repeaters[repeater_id] = repeater
        
        # Send login ACK with salt
        salt_bytes = repeater.salt.to_bytes(4, 'big')
        self._send_packet(b''.join([RPTACK, salt_bytes]), addr)
        LOGGER.info(f'Repeater {int.from_bytes(repeater_id, "big")} login request from {ip}:{port}, sent salt: {repeater.salt}')

    def _handle_auth_response(self, repeater_id: bytes, auth_hash: bytes, addr: PeerAddress) -> None:
        """Handle authentication response from repeater"""
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater or repeater.connection_state != 'login':
            LOGGER.warning(f'Auth response from repeater {int.from_bytes(repeater_id, "big")} in wrong state')
            self._send_nak(repeater_id, addr)
            return
            
        try:
            # Get config for this repeater including its passphrase
            repeater_config = self._matcher.get_repeater_config(
                int.from_bytes(repeater_id, 'big'),
                repeater.callsign.decode().strip() if repeater.callsign else None
            )
            
            # Validate the hash
            salt_bytes = repeater.salt.to_bytes(4, 'big')
            calc_hash = bhex(sha256(b''.join([salt_bytes, repeater_config.passphrase.encode()])).hexdigest().encode())
            
            if auth_hash == calc_hash:
                repeater.authenticated = True
                repeater.connection_state = 'config'
                self._send_packet(b''.join([RPTACK, repeater_id]), addr)
                LOGGER.info(f'Repeater {int.from_bytes(repeater_id, "big")} authenticated successfully')
            else:
                LOGGER.warning(f'Repeater {int.from_bytes(repeater_id, "big")} failed authentication')
                self._send_nak(repeater_id, addr, reason="Authentication failed")
                self._remove_repeater(repeater_id, "auth_failed")
                
        except Exception as e:
            LOGGER.error(f'Authentication error for repeater {int.from_bytes(repeater_id, "big")}: {str(e)}')
            self._send_nak(repeater_id, addr)
            self._remove_repeater(repeater_id, "auth_error")

    def _handle_config(self, data: bytes, addr: PeerAddress) -> None:
        """Handle configuration from repeater"""
        try:
            repeater_id = data[4:8]
            repeater = self._validate_repeater(repeater_id, addr)
            if not repeater or not repeater.authenticated or repeater.connection_state != 'config':
                LOGGER.warning(f'Config from repeater {int.from_bytes(repeater_id, "big")} in wrong state')
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
            LOGGER.debug(f'Repeater {int.from_bytes(repeater_id, "big")} config:'
                      f'\n    Callsign: {repeater.callsign.decode().strip()}'
                      f'\n    RX Freq: {repeater.rx_freq.decode().strip()}'
                      f'\n    TX Freq: {repeater.tx_freq.decode().strip()}'
                      f'\n    Power: {repeater.tx_power.decode().strip()}'
                      f'\n    ColorCode: {repeater.colorcode.decode().strip()}'
                      f'\n    Location: {repeater.location.decode().strip()}'
                      f'\n    Software: {repeater.software_id.decode().strip()}')

            repeater.connected = True
            repeater.connection_state = 'connected'
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)
            LOGGER.info(f'Repeater {int.from_bytes(repeater_id, "big")} ({repeater.callsign.decode().strip()}) configured successfully')
            LOGGER.debug(f'Repeater state after config: id={int.from_bytes(repeater_id, "big")}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
            # Emit repeater_connected event
            try:
                repeater_config = self._matcher.get_repeater_config(
                    int.from_bytes(repeater_id, 'big'),
                    repeater.callsign.decode().strip() if repeater.callsign else None
                )
                talkgroups = repeater_config.talkgroups if repeater_config else []
            except:
                talkgroups = []
            
            self._events.emit('repeater_connected', {
                'repeater_id': int.from_bytes(repeater_id, 'big'),
                'callsign': repeater.callsign.decode().strip() if repeater.callsign else 'UNKNOWN',
                'location': repeater.location.decode().strip() if repeater.location else 'Unknown',
                'address': f'{repeater.ip}:{repeater.port}',
                'talkgroups': talkgroups,
                'last_ping': repeater.last_ping,
                'missed_pings': repeater.missed_pings
            })
            
        except Exception as e:
            LOGGER.error(f'Error parsing config: {str(e)}')
            if 'repeater_id' in locals():
                self._send_nak(repeater_id, addr)

    def _handle_ping(self, repeater_id: bytes, addr: PeerAddress) -> None:
        """Handle ping (RPTPING/RPTP) from the repeater as a keepalive."""
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater or repeater.connection_state != 'connected':
            LOGGER.warning(f'Ping from repeater {int.from_bytes(repeater_id, "big")} in wrong state (state="{repeater.connection_state}" if repeater else "None")')
            self._send_nak(repeater_id, addr, reason="Wrong connection state")
            return
            
        # Update ping time and reset missed pings
        repeater.last_ping = time()
        if repeater.missed_pings > 0:
            LOGGER.info(f'Ping counter reset for repeater {int.from_bytes(repeater_id, "big")} after {repeater.missed_pings} missed pings')
        repeater.missed_pings = 0
        repeater.ping_count += 1
        
        # Send MSTPONG in response to RPTPING/RPTP from repeater
        LOGGER.debug(f'Sending MSTPONG to repeater {int.from_bytes(repeater_id, "big")}')
        self._send_packet(b''.join([MSTPONG, repeater_id]), addr)

    def _handle_disconnect(self, repeater_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater disconnect"""
        repeater = self._validate_repeater(repeater_id, addr)
        if repeater:
            LOGGER.info(f'Repeater {int.from_bytes(repeater_id, "big")} ({repeater.callsign.decode().strip()}) disconnected')
            self._remove_repeater(repeater_id, "disconnect")
            
    def _handle_status(self, repeater_id: bytes, data: bytes, addr: PeerAddress) -> None:
        """Handle repeater status report (including RSSI)"""
        repeater = self._validate_repeater(repeater_id, addr)
        if repeater:
            # TODO: Parse and store RSSI and other status info
            LOGGER.debug(f'Status report from repeater {int.from_bytes(repeater_id, "big")}: {data[8:].hex()}')
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
    
    def _handle_dmr_data(self, data: bytes, addr: PeerAddress) -> None:
        """Handle DMR data"""
        if len(data) < 55:
            LOGGER.warning(f'Invalid DMR data packet from {addr[0]}:{addr[1]} - length {len(data)} < 55')
            return
            
        repeater_id = data[11:15]
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater or repeater.connection_state != 'connected':
            LOGGER.warning(f'DMR data from repeater {int.from_bytes(repeater_id, "big")} in wrong state')
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
            LOGGER.debug(f'Dropped packet from repeater {int.from_bytes(repeater_id, "big")} slot {_slot}: '
                        f'src={int.from_bytes(_rf_src, "big")}, dst={int.from_bytes(_dst_id, "big")}, '
                        f'reason=stream contention or talkgroup not allowed')
            return
        
        # Per-packet logging - only enable for heavy troubleshooting
        #LOGGER.debug(f'DMR data from {int.from_bytes(repeater_id, "big")} slot {_slot}: '
        #            f'seq={_seq}, src={int.from_bytes(_rf_src, "big")}, '
        #            f'dst={int.from_bytes(_dst_id, "big")}, '
        #            f'stream_id={_stream_id.hex()}, '
        #            f'frame_type={_frame_type}, '
        #            f'terminator={_is_terminator}, '
        #            f'packet_count={current_stream.packet_count if current_stream else 0}, '
        #            f'has_lc={current_stream.lc is not None if current_stream else False}')
        
        # Handle terminator frame (immediate stream end detection)
        if _is_terminator and current_stream and not current_stream.ended:
            # DMR terminator detected - end stream immediately and start hang time
            current_stream.ended = True
            current_stream.end_time = time()  # Critical: set end_time for hang calculation!
            hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
            duration = time() - current_stream.start_time
            LOGGER.info(f'RX stream ended on repeater {int.from_bytes(repeater_id, "big")} slot {_slot}: '
                       f'src={int.from_bytes(_rf_src, "big")}, dst={int.from_bytes(_dst_id, "big")}, '
                       f'duration={duration:.2f}s, '
                       f'packets={current_stream.packet_count}, '
                       f'reason=terminator - '
                       f'entering hang time ({hang_time}s)')
            # Emit stream_end event (includes hang_time since they happen together)
            self._events.emit('stream_end', {
                'repeater_id': int.from_bytes(repeater_id, 'big'),
                'slot': _slot,
                'src_id': int.from_bytes(_rf_src, 'big'),
                'dst_id': int.from_bytes(_dst_id, 'big'),
                'duration': round(duration, 2),
                'packets': current_stream.packet_count,
                'reason': 'terminator',
                'hang_time': hang_time,
                'call_type': current_stream.call_type
            })
        
        # Emit stream_update every 60 packets (10 superframes = 1 second)
        if current_stream and not current_stream.ended and current_stream.packet_count % 60 == 0:
            self._events.emit('stream_update', {
                'repeater_id': int.from_bytes(repeater_id, 'big'),
                'slot': _slot,
                'src_id': int.from_bytes(_rf_src, 'big'),
                'dst_id': int.from_bytes(_dst_id, 'big'),
                'duration': round(time() - current_stream.start_time, 2),
                'packets': current_stream.packet_count,
                'talker_alias': current_stream.talker_alias,
                'call_type': current_stream.call_type
            })
        
        # Architecture note:
        # - DMR terminator detection (above) = Primary stream end detection (~60ms after PTT release)
        # - stream_timeout (in _check_stream_timeouts) = Fallback when terminator packet is lost
        # - hang_time = Slot reservation period to prevent hijacking during conversations
        # 
        # This two-tier system ensures:
        #   1. Fast slot turnaround when terminator is received (normal case)
        #   2. Cleanup after timeout when terminator is lost (packet loss case)
        #   3. Slot protection during multi-transmission conversations (hang time)
        
        # Forward DMR data to other connected repeaters
        self._forward_stream(data, repeater_id, _slot, _rf_src, _dst_id, _stream_id)

    def _forward_stream(self, data: bytes, source_repeater_id: bytes, slot: int, 
                       rf_src: bytes, dst_id: bytes, stream_id: bytes) -> None:
        """
        Forward incoming DMR stream packet to appropriate target repeaters.
        
        Phase 3 implementation with configuration-based routing:
        - Check outbound routing rules (TS/TGID tuples)
        - Check slot contention on target repeaters
        - Track assumed stream states on target repeaters
        - Honor hang time after stream completion
        
        Args:
            data: Complete DMRD packet (20-byte HBP header + 33-byte DMR data)
            source_repeater_id: Repeater ID of originating repeater (don't echo back)
            slot: Timeslot (1 or 2)
            rf_src: RF source subscriber ID (3 bytes)
            dst_id: Destination TGID or subscriber ID (3 bytes)  
            stream_id: Unique stream identifier (4 bytes)
        """
        forwarded_count = 0
        tgid = int.from_bytes(dst_id, 'big')
        
        # Check if this is a terminator packet
        _bits = data[15]
        _frame_type = (_bits & 0x30) >> 4
        is_terminator = self._is_dmr_terminator(data, _frame_type)
        
        # Loop through all connected repeaters
        for target_repeater_id, target_repeater in self._repeaters.items():
            # Skip source repeater (don't echo back)
            if target_repeater_id == source_repeater_id:
                continue
            
            # Only forward to fully connected repeaters
            if target_repeater.connection_state != 'connected':
                continue
            
            # Phase 3: Check outbound routing rules (TS/TGID tuples)
            if not self._check_outbound_routing(target_repeater_id, slot, tgid):
                continue
            
            # Phase 2/3: Check contention on target slot
            if self._is_slot_busy(target_repeater_id, slot, stream_id):
                LOGGER.debug(f'Slot busy on target repeater {int.from_bytes(target_repeater_id, "big")} '
                           f'TS{slot}, skipping forward')
                continue
            
            # Forward packet to target repeater
            self._send_packet(data, target_repeater.sockaddr)
            forwarded_count += 1
            
            # Track assumed stream state on target repeater (logs new streams at INFO)
            self._update_assumed_stream(target_repeater, slot, rf_src, dst_id, stream_id, is_terminator, 
                                       int.from_bytes(source_repeater_id, 'big'))
        
        # Log forwarding summary at INFO level
        if forwarded_count > 0:
            LOGGER.debug(f'Forwarded packet: '
                        f'src_repeater={int.from_bytes(source_repeater_id, "big")} '
                        f'slot={slot} subscriber={int.from_bytes(rf_src, "big")} '
                        f'tgid={tgid} -> {forwarded_count} target(s)')
            
            # Emit forwarding event for dashboard
            self._events.emit('stream_forwarded', {
                'source_repeater_id': int.from_bytes(source_repeater_id, 'big'),
                'slot': slot,
                'src_id': int.from_bytes(rf_src, 'big'),
                'dst_id': tgid,
                'stream_id': stream_id.hex(),
                'target_count': forwarded_count,
                'timestamp': time()
            })
    
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
            
            # Log at INFO level - will appear once per target repeater
            LOGGER.info(f'TX stream started on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                       f'from repeater {source_repeater_id}, '
                       f'src={int.from_bytes(rf_src, "big")}, '
                       f'dst={int.from_bytes(dst_id, "big")}')
            
            # Emit stream_start event for dashboard
            self._events.emit('stream_start', {
                'repeater_id': int.from_bytes(repeater.repeater_id, 'big'),
                'slot': slot,
                'src_id': int.from_bytes(rf_src, 'big'),
                'dst_id': int.from_bytes(dst_id, 'big'),
                'call_type': 'group',
                'is_assumed': True
            })
            
            # Update forwarding stats
            self._forwarding_stats['active_calls'] += 1
            self._forwarding_stats['total_calls_today'] += 1
        else:
            # Update existing assumed stream
            current_stream.last_seen = current_time
            current_stream.packet_count += 1
        
        # Handle terminator
        if is_terminator and current_stream:
            duration = current_time - current_stream.start_time
            current_stream.end_time = current_time
            current_stream.ended = True
            hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
            
            # Log at INFO level with consistent format
            LOGGER.info(f'TX stream ended on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                       f'src={int.from_bytes(rf_src, "big")}, '
                       f'dst={int.from_bytes(dst_id, "big")}, '
                       f'duration={duration:.2f}s, '
                       f'packets={current_stream.packet_count}, '
                       f'reason=terminator - '
                       f'entering hang time ({hang_time}s)')
            
            # Emit stream_end event for dashboard
            self._events.emit('stream_end', {
                'repeater_id': int.from_bytes(repeater.repeater_id, 'big'),
                'slot': slot,
                'src_id': int.from_bytes(rf_src, 'big'),
                'dst_id': int.from_bytes(dst_id, 'big'),
                'duration': round(duration, 2),
                'packets': current_stream.packet_count,
                'end_reason': 'terminator',
                'hang_time': CONFIG.get('global', {}).get('stream_hang_time', 3.0),
                'call_type': 'group'
            })
            
            # Update forwarding stats
            self._forwarding_stats['active_calls'] -= 1

    def _send_packet(self, data: bytes, addr: tuple):
        """Send packet to specified address"""
        cmd = data[:4]
        #if cmd != DMRD:  # Don't log DMR data packets
        #    LOGGER.debug(f'Sending {cmd.decode()} to {addr[0]}:{addr[1]}')
        self.transport.write(data, addr)

    def _send_nak(self, repeater_id: bytes, addr: tuple, reason: str = None, is_shutdown: bool = False):
        """Send NAK to specified address
        
        Args:
            repeater_id: The repeater's ID
            addr: The address to send the NAK to
            reason: Why the NAK is being sent
            is_shutdown: Whether this NAK is part of a graceful shutdown
        """
        log_level = logging.DEBUG if is_shutdown else logging.WARNING
        log_msg = f'Sending NAK to {addr[0]}:{addr[1]} for repeater {int.from_bytes(repeater_id, "big")}'
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

def main():
    """Main program entry point"""
    if len(sys.argv) < 2:
        print('Usage: run.py [config/config.json]')
        print('Note: If no config file specified, config/config.json will be used')
        print('      Copy config_sample.json to config.json and edit as needed')
        sys.exit(1)

    load_config(sys.argv[1])
    setup_logging()

    # Create protocol instance so we can access it for cleanup
    protocol = HBProtocol()

    # Listen on UDP port only since HomeBrew DMR only uses UDP
    reactor.listenUDP(
        CONFIG['global']['bind_port'],
        protocol,
        interface=CONFIG['global']['bind_ip']
    )
    
    # Set up signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        """Handle shutdown signals by cleaning up and stopping reactor"""
        signame = signal.Signals(signum).name
        LOGGER.info(f"Received shutdown signal {signame}")
        protocol.cleanup()
        reactor.stop()

    # Register handlers for SIGINT (Ctrl+C) and SIGTERM
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    LOGGER.info(f'HBlink4 server is running on {CONFIG["global"]["bind_ip"]}:{CONFIG["global"]["bind_port"]} (UDP)')
    reactor.run()

if __name__ == '__main__':
    main()
