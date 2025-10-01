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
from typing import Dict, Any, Optional, Tuple, Union
from time import time
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

# Try package-relative imports first, fall back to direct imports
try:
    from .constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, MSTCL,
        DMRD, MSTNAK, MSTPONG, RPTPING, RPTACK, RPTP
    )
    from .access_control import RepeaterMatcher
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, MSTCL,
        DMRD, MSTNAK, MSTPONG, RPTPING, RPTACK, RPTP
    )
    from access_control import RepeaterMatcher

# Type definitions
PeerAddress = Tuple[str, int]

def bhex(data: bytes) -> bytes:
    """Convert hex bytes to bytes, useful for consistent hex handling"""
    return bytes.fromhex(data.decode())

from dataclasses import dataclass, field

@dataclass
class StreamState:
    """Tracks an active DMR transmission stream"""
    radio_id: bytes          # Repeater this stream is on
    rf_src: bytes            # RF source (3 bytes)
    dst_id: bytes            # Destination talkgroup/ID (3 bytes)
    slot: int                # Timeslot (1 or 2)
    start_time: float        # When transmission started
    last_seen: float         # Last packet received
    stream_id: bytes         # Unique stream identifier
    packet_count: int = 0    # Number of packets in this stream
    ended: bool = False      # True when stream has timed out but in hang time
    
    def is_active(self, timeout: float = 2.0) -> bool:
        """Check if stream is still active (within timeout period)"""
        return (time() - self.last_seen) < timeout
    
    def is_in_hang_time(self, timeout: float, hang_time: float) -> bool:
        """Check if stream is in hang time (ended but slot reserved for same source)"""
        if not self.ended:
            return False
        time_since_last = time() - self.last_seen
        return timeout <= time_since_last < (timeout + hang_time)

@dataclass
class RepeaterState:
    """Data class for storing repeater state"""
    radio_id: bytes
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
        self._port = None  # Store the port instance instead of transport
        
    def cleanup(self) -> None:
        """Send disconnect messages to all repeaters and cleanup resources."""
        LOGGER.info("Starting graceful shutdown...")
        
        # Send MSTCL to all connected repeaters
        if self._port:  # Only attempt to send if we have a port
            for radio_id, repeater in self._repeaters.items():
                if repeater.connection_state == 'yes':
                    try:
                        LOGGER.info(f"Sending disconnect to repeater {int.from_bytes(radio_id, 'big')}")
                        self._port.write(MSTCL, repeater.sockaddr)
                    except Exception as e:
                        LOGGER.error(f"Error sending disconnect to repeater {int.from_bytes(radio_id, 'big')}: {e}")

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

    def stopProtocol(self):
        """Called when transport is disconnected"""
        if self._timeout_task and self._timeout_task.running:
            self._timeout_task.stop()
        if self._stream_timeout_task and self._stream_timeout_task.running:
            self._stream_timeout_task.stop()
            
    def _check_repeater_timeouts(self):
        """Check for and handle repeater timeouts. Repeaters should send periodic RPTPING/RPTP."""
        current_time = time()
        timeout_duration = CONFIG.get('timeout', {}).get('repeater', 30)  # 30 second default
        max_missed = CONFIG.get('timeout', {}).get('max_missed', 3)  # 3 missed pings default
        
        # Make a list to avoid modifying dict during iteration
        for radio_id, repeater in list(self._repeaters.items()):
            if repeater.connection_state != 'connected':
                continue
                
            time_since_ping = current_time - repeater.last_ping
            
            if time_since_ping > timeout_duration:
                repeater.missed_pings += 1
                LOGGER.warning(f'Repeater {int.from_bytes(radio_id, "big")} missed ping #{repeater.missed_pings}')
                
                if repeater.missed_pings >= max_missed:
                    LOGGER.error(f'Repeater {int.from_bytes(radio_id, "big")} timed out after {repeater.missed_pings} missed pings')
                    # Send NAK to trigger re-registration
                    self._send_nak(radio_id, (repeater.ip, repeater.port), reason=f"Timeout after {repeater.missed_pings} missed pings")
                    self._remove_repeater(radio_id, "timeout")
    
    def _check_stream_timeouts(self):
        """Check for and clean up stale streams on all repeaters"""
        current_time = time()
        stream_timeout = CONFIG.get('global', {}).get('stream_timeout', 2.0)
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 3.0)
        
        for radio_id, repeater in self._repeaters.items():
            if repeater.connection_state != 'connected':
                continue
            
            # Check slot 1
            if repeater.slot1_stream:
                stream = repeater.slot1_stream
                if not stream.is_active(stream_timeout):
                    if not stream.ended:
                        # Stream just ended - mark it and start hang time
                        stream.ended = True
                        LOGGER.info(f'Stream ended on repeater {int.from_bytes(radio_id, "big")} slot 1: '
                                   f'src={int.from_bytes(stream.rf_src, "big")}, '
                                   f'dst={int.from_bytes(stream.dst_id, "big")}, '
                                   f'duration={current_time - stream.start_time:.2f}s, '
                                   f'packets={stream.packet_count} - '
                                   f'entering hang time ({hang_time}s)')
                    elif not stream.is_in_hang_time(stream_timeout, hang_time):
                        # Hang time expired - clear the slot
                        LOGGER.debug(f'Hang time expired on repeater {int.from_bytes(radio_id, "big")} slot 1')
                        repeater.slot1_stream = None
            
            # Check slot 2
            if repeater.slot2_stream:
                stream = repeater.slot2_stream
                if not stream.is_active(stream_timeout):
                    if not stream.ended:
                        # Stream just ended - mark it and start hang time
                        stream.ended = True
                        LOGGER.info(f'Stream ended on repeater {int.from_bytes(radio_id, "big")} slot 2: '
                                   f'src={int.from_bytes(stream.rf_src, "big")}, '
                                   f'dst={int.from_bytes(stream.dst_id, "big")}, '
                                   f'duration={current_time - stream.start_time:.2f}s, '
                                   f'packets={stream.packet_count} - '
                                   f'entering hang time ({hang_time}s)')
                    elif not stream.is_in_hang_time(stream_timeout, hang_time):
                        # Hang time expired - clear the slot
                        LOGGER.debug(f'Hang time expired on repeater {int.from_bytes(radio_id, "big")} slot 2')
                        repeater.slot2_stream = None

    def datagramReceived(self, data: bytes, addr: tuple):
        """Handle received UDP datagram"""
        ip, port = addr
        
        # Debug log the raw packet
        #LOGGER.debug(f'Raw packet from {ip}:{port}: {data.hex()}')
            
        _command = data[:4]
        LOGGER.debug(f'Command bytes: {_command}')
        
        try:
            # Extract radio_id based on packet type
            radio_id = None
            if _command == DMRD:
                radio_id = data[11:15]
            elif _command == RPTP:
                radio_id = data[7:11]
            elif _command == RPTL:
                radio_id = data[4:8]
            elif _command == RPTK:
                radio_id = data[4:8]
            elif _command == RPTC:
                if data[:5] == RPTCL:
                    radio_id = data[5:9]
                else:
                    radio_id = data[4:8]
                
            if radio_id:
                LOGGER.debug(f'Packet received: cmd={_command}, radio_id={int.from_bytes(radio_id, "big")}, addr={addr}')
            else:
                LOGGER.warning(f'Packet received with unknown command: cmd={_command}, radio_id={int.from_bytes(radio_id, "big")}, addr={addr}')   
                return
            
            # Update ping time for connected repeaters
            if radio_id and radio_id in self._repeaters:
                repeater = self._repeaters[radio_id]
                if repeater.connection_state == 'connected':
                    repeater.last_ping = time()
                    repeater.missed_pings = 0

            # Process the packet
            if _command == DMRD:
                self._handle_dmr_data(data, addr)
            elif _command == RPTL:
                LOGGER.debug(f'Received RPTL from {ip}:{port} - Repeater Login Request')
                self._handle_repeater_login(radio_id, addr)
            elif len(data) == 4:  # Special case: raw repeater ID login
                # Try to interpret as a raw repeater ID
                LOGGER.debug(f'Received possible raw repeater ID login from {ip}:{port}')
                self._handle_repeater_login(data, addr)
            elif _command == RPTK:
                LOGGER.debug(f'Received RPTK from {ip}:{port} - Authentication Response')
                self._handle_auth_response(radio_id, data[8:], addr)
            elif _command == RPTC:
                if data[:5] == RPTCL:
                    LOGGER.debug(f'Received RPTCL from {ip}:{port} - Disconnect Request')
                    self._handle_disconnect(radio_id, addr)
                else:
                    LOGGER.debug(f'Received RPTC from {ip}:{port} - Configuration Data')
                    self._handle_config(data, addr)
            elif _command[:4] == RPTP:  # Check just RPTP prefix since that's enough to identify RPTPING
                LOGGER.debug(f'Received RPTPING from {ip}:{port} - Repeater Keepalive')
                self._handle_ping(radio_id, addr)
            else:
                LOGGER.warning(f'Unknown command received from {ip}:{port}: {_command}')
        except Exception as e:
            LOGGER.error(f'Error processing datagram from {ip}:{port}: {str(e)}')

    def _validate_repeater(self, radio_id: bytes, addr: PeerAddress) -> Optional[RepeaterState]:
        """Validate repeater state and address"""
        if radio_id not in self._repeaters:
            LOGGER.debug(f'Repeater {int.from_bytes(radio_id, "big")} not found in _repeaters dict')
            self._send_nak(radio_id, addr, reason="Repeater not registered")
            return None
            
        repeater = self._repeaters[radio_id]
        LOGGER.debug(f'Validating repeater {int.from_bytes(radio_id, "big")}: state="{repeater.connection_state}", stored_addr={repeater.sockaddr}, incoming_addr={addr}')
        
        if repeater.sockaddr != addr:
            LOGGER.warning(f'Message from wrong IP for repeater {int.from_bytes(radio_id, "big")}')
            self._send_nak(radio_id, addr, reason="Message from incorrect IP address")
            return None
            
        return repeater
    
    def _is_talkgroup_allowed(self, repeater: RepeaterState, dst_id: bytes) -> bool:
        """Check if a talkgroup is allowed for this repeater based on its configuration"""
        try:
            # Get the repeater's configuration
            repeater_config = self._matcher.get_repeater_config(
                int.from_bytes(repeater.radio_id, 'big'),
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
                             slot: int, stream_id: bytes) -> bool:
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
                    LOGGER.info(f'Same source resuming on repeater {int.from_bytes(repeater.radio_id, "big")} slot {slot} '
                               f'during hang time: src={int.from_bytes(rf_src, "big")}, '
                               f'old_dst={int.from_bytes(current_stream.dst_id, "big")}, '
                               f'new_dst={int.from_bytes(dst_id, "big")}')
                    # Allow by falling through to create new stream
                else:
                    LOGGER.warning(f'Hang time contention on repeater {int.from_bytes(repeater.radio_id, "big")} slot {slot}: '
                                  f'slot reserved for src={int.from_bytes(current_stream.rf_src, "big")}, '
                                  f'denied src={int.from_bytes(rf_src, "big")}')
                    return False
            else:
                # Active stream - different stream_id means contention
                LOGGER.warning(f'Stream contention on repeater {int.from_bytes(repeater.radio_id, "big")} slot {slot}: '
                              f'existing stream (src={int.from_bytes(current_stream.rf_src, "big")}, '
                              f'dst={int.from_bytes(current_stream.dst_id, "big")}) '
                              f'vs new stream (src={int.from_bytes(rf_src, "big")}, '
                              f'dst={int.from_bytes(dst_id, "big")})')
                
                # Deny the new stream - first come, first served
                return False
        
        # Check if talkgroup is allowed for this repeater
        if not self._is_talkgroup_allowed(repeater, dst_id):
            LOGGER.warning(f'Talkgroup {int.from_bytes(dst_id, "big")} not allowed on repeater '
                          f'{int.from_bytes(repeater.radio_id, "big")} slot {slot}')
            return False
        
        # No active stream, start a new one
        new_stream = StreamState(
            radio_id=repeater.radio_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            start_time=current_time,
            last_seen=current_time,
            stream_id=stream_id,
            packet_count=1
        )
        
        repeater.set_slot_stream(slot, new_stream)
        
        LOGGER.info(f'Stream started on repeater {int.from_bytes(repeater.radio_id, "big")} slot {slot}: '
                   f'src={int.from_bytes(rf_src, "big")}, dst={int.from_bytes(dst_id, "big")}, '
                   f'stream_id={stream_id.hex()}')
        
        return True
    
    def _handle_stream_packet(self, repeater: RepeaterState, rf_src: bytes, dst_id: bytes,
                              slot: int, stream_id: bytes) -> bool:
        """
        Handle a packet for an ongoing stream.
        Returns True if the packet is valid for the current stream, False otherwise.
        """
        current_stream = repeater.get_slot_stream(slot)
        
        if not current_stream:
            # No active stream - this is a new stream
            return self._handle_stream_start(repeater, rf_src, dst_id, slot, stream_id)
        
        # Check if this packet belongs to the current stream
        if current_stream.stream_id != stream_id:
            # Different stream - contention
            return False
        
        # Update stream state
        current_stream.last_seen = time()
        current_stream.packet_count += 1
        
        return True
        
    def _remove_repeater(self, radio_id: bytes, reason: str) -> None:
        """
        Remove a repeater and clean up all its state.
        This ensures we don't have any memory leaks from lingering references.
        """
        if radio_id in self._repeaters:
            repeater = self._repeaters[radio_id]
            
            # Log current state before removal
            LOGGER.debug(f'Removing repeater {int.from_bytes(radio_id, "big")}: reason={reason}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
            # Remove from active repeaters
            del self._repeaters[radio_id]
            

    def _handle_repeater_login(self, radio_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater login request"""
        ip, port = addr
        
        LOGGER.debug(f'Processing login for repeater ID {int.from_bytes(radio_id, "big")} from {ip}:{port}')
        
        if radio_id in self._repeaters:
            repeater = self._repeaters[radio_id]
            if repeater.sockaddr != addr:
                LOGGER.warning(f'Repeater {int.from_bytes(radio_id, "big")} attempting to connect from {ip}:{port} but already connected from {repeater.ip}:{repeater.port}')
                # Remove the old registration first
                old_addr = repeater.sockaddr
                self._remove_repeater(radio_id, "reconnect_different_port")
                # Then send NAK to the old address to ensure cleanup
                self._send_nak(radio_id, old_addr, reason="Repeater reconnecting from new address")
                # Continue with new connection below
            else:
                # Same repeater reconnecting from same IP:port
                old_state = repeater.connection_state
                LOGGER.info(f'Repeater {int.from_bytes(radio_id, "big")} reconnecting while in state {old_state}')
                
        # Create or update repeater state
        repeater = RepeaterState(radio_id=radio_id, ip=ip, port=port)
        repeater.connection_state = 'login'
        self._repeaters[radio_id] = repeater
        
        # Send login ACK with salt
        salt_bytes = repeater.salt.to_bytes(4, 'big')
        self._send_packet(b''.join([RPTACK, salt_bytes]), addr)
        LOGGER.info(f'Repeater {int.from_bytes(radio_id, "big")} login request from {ip}:{port}, sent salt: {repeater.salt}')

    def _handle_auth_response(self, radio_id: bytes, auth_hash: bytes, addr: PeerAddress) -> None:
        """Handle authentication response from repeater"""
        repeater = self._validate_repeater(radio_id, addr)
        if not repeater or repeater.connection_state != 'login':
            LOGGER.warning(f'Auth response from repeater {int.from_bytes(radio_id, "big")} in wrong state')
            self._send_nak(radio_id, addr)
            return
            
        try:
            # Get config for this repeater including its passphrase
            repeater_config = self._matcher.get_repeater_config(
                int.from_bytes(radio_id, 'big'),
                repeater.callsign.decode().strip() if repeater.callsign else None
            )
            
            # Validate the hash
            salt_bytes = repeater.salt.to_bytes(4, 'big')
            calc_hash = bhex(sha256(b''.join([salt_bytes, repeater_config.passphrase.encode()])).hexdigest().encode())
            
            if auth_hash == calc_hash:
                repeater.authenticated = True
                repeater.connection_state = 'config'
                self._send_packet(b''.join([RPTACK, radio_id]), addr)
                LOGGER.info(f'Repeater {int.from_bytes(radio_id, "big")} authenticated successfully')
            else:
                LOGGER.warning(f'Repeater {int.from_bytes(radio_id, "big")} failed authentication')
                self._send_nak(radio_id, addr, reason="Authentication failed")
                self._remove_repeater(radio_id, "auth_failed")
                
        except Exception as e:
            LOGGER.error(f'Authentication error for repeater {int.from_bytes(radio_id, "big")}: {str(e)}')
            self._send_nak(radio_id, addr)
            self._remove_repeater(radio_id, "auth_error")

    def _handle_config(self, data: bytes, addr: PeerAddress) -> None:
        """Handle configuration from repeater"""
        try:
            radio_id = data[4:8]
            repeater = self._validate_repeater(radio_id, addr)
            if not repeater or not repeater.authenticated or repeater.connection_state != 'config':
                LOGGER.warning(f'Config from repeater {int.from_bytes(radio_id, "big")} in wrong state')
                self._send_nak(radio_id, addr)
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
            LOGGER.debug(f'Repeater {int.from_bytes(radio_id, "big")} config:'
                      f'\n    Callsign: {repeater.callsign.decode().strip()}'
                      f'\n    RX Freq: {repeater.rx_freq.decode().strip()}'
                      f'\n    TX Freq: {repeater.tx_freq.decode().strip()}'
                      f'\n    Power: {repeater.tx_power.decode().strip()}'
                      f'\n    ColorCode: {repeater.colorcode.decode().strip()}'
                      f'\n    Location: {repeater.location.decode().strip()}'
                      f'\n    Software: {repeater.software_id.decode().strip()}')

            repeater.connected = True
            repeater.connection_state = 'connected'
            self._send_packet(b''.join([RPTACK, radio_id]), addr)
            LOGGER.info(f'Repeater {int.from_bytes(radio_id, "big")} ({repeater.callsign.decode().strip()}) configured successfully')
            LOGGER.debug(f'Repeater state after config: id={int.from_bytes(radio_id, "big")}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
        except Exception as e:
            LOGGER.error(f'Error parsing config: {str(e)}')
            if 'radio_id' in locals():
                self._send_nak(radio_id, addr)

    def _handle_ping(self, radio_id: bytes, addr: PeerAddress) -> None:
        """Handle ping (RPTPING/RPTP) from the repeater as a keepalive."""
        repeater = self._validate_repeater(radio_id, addr)
        if not repeater or repeater.connection_state != 'connected':
            LOGGER.warning(f'Ping from repeater {int.from_bytes(radio_id, "big")} in wrong state (state="{repeater.connection_state}" if repeater else "None")')
            self._send_nak(radio_id, addr, reason="Wrong connection state")
            return
            
        # Update ping time and reset missed pings
        repeater.last_ping = time()
        if repeater.missed_pings > 0:
            LOGGER.info(f'Ping counter reset for repeater {int.from_bytes(radio_id, "big")} after {repeater.missed_pings} missed pings')
        repeater.missed_pings = 0
        repeater.ping_count += 1
        
        # Send MSTPONG in response to RPTPING/RPTP from repeater
        LOGGER.debug(f'Sending MSTPONG to repeater {int.from_bytes(radio_id, "big")}')
        self._send_packet(b''.join([MSTPONG, radio_id]), addr)

    def _handle_disconnect(self, radio_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater disconnect"""
        repeater = self._validate_repeater(radio_id, addr)
        if repeater:
            LOGGER.info(f'Repeater {int.from_bytes(radio_id, "big")} ({repeater.callsign.decode().strip()}) disconnected')
            self._remove_repeater(radio_id, "disconnect")
            
    def _handle_status(self, radio_id: bytes, data: bytes, addr: PeerAddress) -> None:
        """Handle repeater status report (including RSSI)"""
        repeater = self._validate_repeater(radio_id, addr)
        if repeater:
            # TODO: Parse and store RSSI and other status info
            LOGGER.debug(f'Status report from repeater {int.from_bytes(radio_id, "big")}: {data[8:].hex()}')
            self._send_packet(b''.join([RPTACK, radio_id]), addr)

    def _is_dmr_terminator(self, data: bytes, frame_type: int) -> bool:
        """
        Check if a DMR packet is a stream terminator.
        
        DMR terminators have:
        - Frame type = Voice Sync (0x01) or Data Sync (0x02)
        - Specific sync pattern in the payload indicating terminator vs header
        
        Returns True if this is a terminator frame, False otherwise.
        
        TODO: Implement full DMR sync pattern detection
        Currently returns False, relying on timeout-based stream end detection.
        """
        # Voice Sync and Data Sync frames can be headers or terminators
        # Need to check the sync pattern in the payload to distinguish
        if frame_type not in [0x01, 0x02]:
            return False
        
        # TODO: Decode sync pattern from data[20:53] to determine if terminator
        # DMR Sync patterns:
        #   - Voice Header:     0x7555FD7DFF771755
        #   - Voice Terminator: 0x7555FD7DFF771755 (same as header, but context differs)
        #   - Data Header:      0xDFF57D75DF5D
        #   - Data Terminator:  0xDFF57D75DF5D
        # 
        # The actual determination requires looking at the embedded signaling
        # and sequence within the payload, not just the sync pattern itself.
        
        return False  # Not implemented yet - using timeout fallback
    
    def _handle_dmr_data(self, data: bytes, addr: PeerAddress) -> None:
        """Handle DMR data"""
        if len(data) < 55:
            LOGGER.warning(f'Invalid DMR data packet from {addr[0]}:{addr[1]}')
            return
            
        radio_id = data[11:15]
        repeater = self._validate_repeater(radio_id, addr)
        if not repeater or repeater.connection_state != 'connected':
            LOGGER.warning(f'DMR data from repeater {int.from_bytes(radio_id, "big")} in wrong state')
            return
            
        # Extract packet information
        _seq = data[4]
        _rf_src = data[5:8]
        _dst_id = data[8:11]
        _bits = data[15]
        _slot = 2 if (_bits & 0x80) else 1
        _call_type = (_bits & 0x40) >> 6  # 0 = private, 1 = group
        _frame_type = (_bits & 0x30) >> 4  # 0 = voice, 1 = voice sync, 2 = data sync, 3 = unused
        _stream_id = data[16:20]  # Stream ID for tracking unique transmissions
        
        # Check if this is a stream terminator (immediate end detection)
        _is_terminator = self._is_dmr_terminator(data, _frame_type)
        
        # Handle stream tracking
        stream_valid = self._handle_stream_packet(repeater, _rf_src, _dst_id, _slot, _stream_id)
        
        if not stream_valid:
            # Stream contention or not allowed - drop packet silently
            LOGGER.debug(f'Dropped packet from repeater {int.from_bytes(radio_id, "big")} slot {_slot}: '
                        f'src={int.from_bytes(_rf_src, "big")}, dst={int.from_bytes(_dst_id, "big")}, '
                        f'reason=stream contention or talkgroup not allowed')
            return
        
        # Log packet details at debug level
        current_stream = repeater.get_slot_stream(_slot)
        LOGGER.debug(f'DMR data from {int.from_bytes(radio_id, "big")} slot {_slot}: '
                    f'seq={_seq}, src={int.from_bytes(_rf_src, "big")}, '
                    f'dst={int.from_bytes(_dst_id, "big")}, '
                    f'stream_id={_stream_id.hex()}, '
                    f'frame_type={_frame_type}, '
                    f'terminator={_is_terminator}, '
                    f'packet_count={current_stream.packet_count if current_stream else 0}')
        
        # Handle terminator frame (immediate stream end detection)
        if _is_terminator and current_stream and not current_stream.ended:
            # DMR terminator detected - end stream immediately and start hang time
            current_stream.ended = True
            hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
            LOGGER.info(f'DMR terminator received on repeater {int.from_bytes(radio_id, "big")} slot {_slot}: '
                       f'src={int.from_bytes(_rf_src, "big")}, dst={int.from_bytes(_dst_id, "big")}, '
                       f'duration={time() - current_stream.start_time:.2f}s, '
                       f'packets={current_stream.packet_count} - '
                       f'entering hang time ({hang_time}s)')
        
        # Architecture note:
        # - DMR terminator detection (above) = Primary stream end detection (~60ms after PTT release)
        # - stream_timeout (in _check_stream_timeouts) = Fallback when terminator packet is lost
        # - hang_time = Slot reservation period to prevent hijacking during conversations
        # 
        # This two-tier system ensures:
        #   1. Fast slot turnaround when terminator is received (normal case)
        #   2. Cleanup after timeout when terminator is lost (packet loss case)
        #   3. Slot protection during multi-transmission conversations (hang time)
        
        # TODO: Implement DMR data routing/forwarding logic here
        # For now, we're just tracking streams without forwarding

    def _send_packet(self, data: bytes, addr: tuple):
        """Send packet to specified address"""
        cmd = data[:4]
        #if cmd != DMRD:  # Don't log DMR data packets
        #    LOGGER.debug(f'Sending {cmd.decode()} to {addr[0]}:{addr[1]}')
        self.transport.write(data, addr)

    def _send_nak(self, radio_id: bytes, addr: tuple, reason: str = None, is_shutdown: bool = False):
        """Send NAK to specified address
        
        Args:
            radio_id: The repeater's ID
            addr: The address to send the NAK to
            reason: Why the NAK is being sent
            is_shutdown: Whether this NAK is part of a graceful shutdown
        """
        log_level = logging.DEBUG if is_shutdown else logging.WARNING
        log_msg = f'Sending NAK to {addr[0]}:{addr[1]} for repeater {int.from_bytes(radio_id, "big")}'
        if reason:
            log_msg += f' - {reason}'
        
        LOGGER.log(log_level, log_msg)
        self._send_packet(b''.join([MSTNAK, radio_id]), addr)


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
