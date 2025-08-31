#!/usr/bin/env python3
"""
Copyright (c) 2025 by Cort Buffington, N0MJS

A complete architectural redesign of HBlink3, implementing 
approach to DMR server services. The HomeBrew DMR protocol is UDP-based, used for 
communication between DMR repeaters and servers.

License: GNU GPLv3
"""

import json
import logging
import pathlib
from typing import Dict, Any, Optional, Tuple, Union
from time import time
from random import randint
from hashlib import sha256

import signal
from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol

# Global configuration dictionary
CONFIG: Dict[str, Any] = {}
LOGGER = logging.getLogger(__name__)

import os
import sys

# Try package-relative imports first, fall back to direct imports
try:
    from .constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, MSTCL,
        DMRD, MSTNAK, MSTP, MSTPONG, RPTACK, RPTP
    )
    from .access_control import RepeaterMatcher
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, MSTCL,
        DMRD, MSTNAK, MSTP, MSTPONG, RPTACK, RPTP
    )
    from access_control import RepeaterMatcher

# Type definitions
PeerAddress = Tuple[str, int]

def bhex(data: bytes) -> bytes:
    """Convert hex bytes to bytes, useful for consistent hex handling"""
    return bytes.fromhex(data.decode())

from dataclasses import dataclass, field

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
    connection_state: str = 'no'  # States: no, rptl-received, waiting-config, yes
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
    
    @property
    def sockaddr(self) -> PeerAddress:
        """Get socket address tuple"""
        return (self.ip, self.port)

class HBProtocol(DatagramProtocol):
    """UDP Implementation of HomeBrew DMR Server Protocol"""
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._repeaters: Dict[bytes, RepeaterState] = {}
        self._active_ids: set[bytes] = set()  # Just store IDs of fully connected repeaters
        self._config = CONFIG
        self._matcher = RepeaterMatcher(CONFIG)
        self._timeout_task = None
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

    def stopProtocol(self):
        """Called when transport is disconnected"""
        if self._timeout_task and self._timeout_task.running:
            self._timeout_task.stop()
            
    def _check_repeater_timeouts(self):
        """Check for and handle repeater timeouts"""
        current_time = time()
        timeout_duration = CONFIG.get('timeout', {}).get('repeater', 30)  # 30 second default
        max_missed = CONFIG.get('timeout', {}).get('max_missed', 3)  # 3 missed pings default
        
        # Make a list to avoid modifying dict during iteration
        for radio_id, repeater in list(self._repeaters.items()):
            time_since_ping = current_time - repeater.last_ping
            
            if time_since_ping > timeout_duration:
                repeater.missed_pings += 1
                LOGGER.warning(f'Repeater {int.from_bytes(radio_id, "big")} missed ping #{repeater.missed_pings}')
                
                if repeater.missed_pings >= max_missed:
                    LOGGER.error(f'Repeater {int.from_bytes(radio_id, "big")} timed out after {repeater.missed_pings} missed pings')
                    self._remove_repeater(radio_id, "timeout")

    def datagramReceived(self, data: bytes, addr: tuple):
        """Handle received UDP datagram"""
        ip, port = addr
        
        # Debug log the raw packet
        LOGGER.debug(f'Raw packet from {ip}:{port}: {data.hex()}')
        
        # Very first check - is this a 4-byte login packet?
        if len(data) == 4:
            try:
                repeater_id = int.from_bytes(data, 'big')
                LOGGER.debug(f'Treating 4-byte packet as repeater login from {ip}:{port}, ID: {repeater_id}')
                self._handle_repeater_login(data, addr)
            except Exception as e:
                LOGGER.error(f'Error processing potential repeater login from {ip}:{port}: {e}')
            return  # Always return after handling 4-byte packet
            
        # For all other packets, must be at least 4 bytes for command
        if len(data) < 4:
            LOGGER.warning(f'Received undersized datagram from {ip}:{port}: {data}')
            return
            
        _command = data[:4]
        LOGGER.debug(f'Command bytes: {_command}')
        
        try:
            # Extract radio_id based on packet type
            radio_id = None
            if _command == DMRD:
                radio_id = data[11:15]
            elif _command == RPTL:
                radio_id = data[4:8]
            elif _command == RPTK:
                radio_id = data[4:8]
            elif _command == RPTC:
                if data[:5] == RPTCL:
                    radio_id = data[5:9]
                else:
                    radio_id = data[4:8]
            elif _command == RPTP:
                radio_id = data[7:11]  # Fixed offset for RPTP packets
                
            if radio_id:
                LOGGER.debug(f'Packet received: cmd={_command}, radio_id={int.from_bytes(radio_id, "big")}, addr={addr}')
            
            # Update ping time for connected repeaters
            if radio_id and radio_id in self._repeaters:
                repeater = self._repeaters[radio_id]
                if repeater.connection_state == 'yes':
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
            elif _command == RPTP:
                LOGGER.debug(f'Received RPTP from {ip}:{port} - Keepalive Request')
                self._handle_ping(radio_id, addr)
            else:
                LOGGER.warning(f'Unknown command received from {ip}:{port}: {_command}')
        except Exception as e:
            LOGGER.error(f'Error processing datagram from {ip}:{port}: {str(e)}')

    def _validate_repeater(self, radio_id: bytes, addr: PeerAddress) -> Optional[RepeaterState]:
        """Validate repeater state and address"""
        if radio_id not in self._repeaters:
            LOGGER.debug(f'Repeater {int.from_bytes(radio_id, "big")} not found in _repeaters dict')
            return None
            
        repeater = self._repeaters[radio_id]
        LOGGER.debug(f'Validating repeater {int.from_bytes(radio_id, "big")}: state="{repeater.connection_state}", stored_addr={repeater.sockaddr}, incoming_addr={addr}')
        
        if repeater.sockaddr != addr:
            LOGGER.warning(f'Message from wrong IP for repeater {int.from_bytes(radio_id, "big")}')
            self._send_nak(radio_id, addr)
            return None
            
        return repeater
        
    def _remove_repeater(self, radio_id: bytes, reason: str) -> None:
        """
        Remove a repeater and clean up all its state.
        This ensures we don't have any memory leaks from lingering references.
        """
        if radio_id in self._repeaters:
            repeater = self._repeaters[radio_id]
            
            # Log current state before removal
            LOGGER.debug(f'Removing repeater {int.from_bytes(radio_id, "big")}: reason={reason}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
            # Clear all dynamic state
            repeater.authenticated = False
            repeater.connected = False
            repeater.connection_state = 'no'
            repeater.last_ping = 0
            repeater.ping_count = 0
            repeater.missed_pings = 0
            repeater.last_rssi = 0
            repeater.rssi_count = 0
            
            # Remove from active repeaters
            del self._repeaters[radio_id]
            
            # Force garbage collection of any circular references
            repeater = None

    def _handle_repeater_login(self, radio_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater login request"""
        ip, port = addr
        
        LOGGER.debug(f'Processing login for repeater ID {int.from_bytes(radio_id, "big")} from {ip}:{port}')
        
        if radio_id in self._repeaters:
            repeater = self._repeaters[radio_id]
            if repeater.sockaddr != addr:
                LOGGER.warning(f'Repeater {int.from_bytes(radio_id, "big")} attempting to connect from {ip}:{port} but already connected from {repeater.ip}:{repeater.port}')
                self._send_nak(radio_id, addr)
                return
            else:
                # Same repeater reconnecting from same IP:port
                old_state = repeater.connection_state
                LOGGER.info(f'Repeater {int.from_bytes(radio_id, "big")} reconnecting while in state {old_state}')
                
        # Create or update repeater state
        repeater = RepeaterState(radio_id=radio_id, ip=ip, port=port)
        self._set_repeater_state(radio_id, 'rptl-received')
        self._repeaters[radio_id] = repeater
        
        # Send login ACK with salt
        salt_bytes = repeater.salt.to_bytes(4, 'big')
        self._send_packet(b''.join([RPTACK, salt_bytes]), addr)
        LOGGER.info(f'Repeater {int.from_bytes(radio_id, "big")} login request from {ip}:{port}, sent salt: {repeater.salt}')

    def _handle_auth_response(self, radio_id: bytes, auth_hash: bytes, addr: PeerAddress) -> None:
        """Handle authentication response from repeater"""
        repeater = self._validate_repeater(radio_id, addr)
        if not repeater or repeater.connection_state != 'rptl-received':
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
                self._set_repeater_state(radio_id, 'waiting-config')
                self._send_packet(b''.join([RPTACK, radio_id]), addr)
                LOGGER.info(f'Repeater {int.from_bytes(radio_id, "big")} authenticated successfully')
            else:
                LOGGER.warning(f'Repeater {int.from_bytes(radio_id, "big")} failed authentication')
                self._send_nak(radio_id, addr)
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
            if not repeater or not repeater.authenticated or repeater.connection_state != 'waiting-config':
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
            self._set_repeater_state(radio_id, 'yes')  # Match HBlink3 state case
            self._send_packet(b''.join([RPTACK, radio_id]), addr)
            LOGGER.info(f'Repeater {int.from_bytes(radio_id, "big")} ({repeater.callsign.decode().strip()}) configured successfully')
            LOGGER.debug(f'Repeater state after config: id={int.from_bytes(radio_id, "big")}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
        except Exception as e:
            LOGGER.error(f'Error parsing config: {str(e)}')
            if 'radio_id' in locals():
                self._send_nak(radio_id, addr)

    def _handle_ping(self, radio_id: bytes, addr: PeerAddress) -> None:
        """Handle ping from repeater"""
        repeater = self._validate_repeater(radio_id, addr)
        if not repeater or repeater.connection_state != 'yes':
            LOGGER.warning(f'Ping from repeater {int.from_bytes(radio_id, "big")} in wrong state (state="{repeater.connection_state}" if repeater else "None")')
            self._send_nak(radio_id, addr)
            return
            
        # Only increment ping count for explicit pings
        repeater.ping_count += 1
        self._send_packet(b''.join([MSTPONG, radio_id]), addr)  # Changed to use MSTPONG

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

    def _set_repeater_state(self, radio_id: bytes, state: str) -> None:
        """Update repeater state and maintain active set"""
        if state == 'yes':
            self._active_ids.add(radio_id)
        else:
            self._active_ids.discard(radio_id)
        self._repeaters[radio_id].connection_state = state

    def _handle_dmr_data(self, data: bytes, addr: PeerAddress) -> None:
        """Handle DMR data"""
        if len(data) < 55:
            LOGGER.warning(f'Invalid DMR data packet from {addr[0]}:{addr[1]}')
            return
            
        source_id = data[11:15]
        if source_id not in self._active_ids:
            return  # Ignore packets from inactive repeaters
            
        # Extract packet information for logging only
        seq = data[4]
        rf_src = data[5:8]
        dst_id = data[8:11]
        bits = data[15]
        slot = 2 if (bits & 0x80) else 1
        
        if LOGGER.isEnabledFor(logging.DEBUG):
            LOGGER.debug(f'DMR data from {int.from_bytes(source_id, "big")}: seq={seq}, src={int.from_bytes(rf_src, "big")}, dst={int.from_bytes(dst_id, "big")}, slot={slot}')
            
        # Only forward if configured to do so
        if self._config.get('global', {}).get('forward_dmr', True):
            for target_id in self._active_ids:
                if target_id != source_id:  # Don't send back to source
                    target_repeater = self._repeaters[target_id]
                    self._port.write(data, target_repeater.sockaddr)

    def _send_packet(self, data: bytes, addr: tuple):
        """Send packet to specified address"""
        cmd = data[:4]
        if cmd != DMRD:  # Don't log DMR data packets
            LOGGER.debug(f'Sending {cmd.decode()} to {addr[0]}:{addr[1]}')
        self.transport.write(data, addr)

    def _send_nak(self, radio_id: bytes, addr: tuple):
        """Send NAK to specified address"""
        LOGGER.debug(f'Sending NAK to {addr[0]}:{addr[1]} for repeater {int.from_bytes(radio_id, "big")}')
        self._send_packet(b''.join([MSTNAK, radio_id]), addr)



from twisted.internet.task import LoopingCall

def setup_logging():
    """Configure logging"""
    logging_config = CONFIG.get('global', {}).get('logging', {})
    
    # Use old config format if new one not present
    if not logging_config:
        log_level = getattr(logging, CONFIG['global'].get('log_level', 'INFO'))
        log_file = CONFIG['global'].get('log_file', 'logs/hblink.log')
        file_level = console_level = log_level
    else:
        log_file = logging_config.get('file', 'logs/hblink.log')
        file_level = getattr(logging, logging_config.get('file_level', 'DEBUG'))
        console_level = getattr(logging, logging_config.get('console_level', 'INFO'))
    
    log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # Create log directory if it doesn't exist
    pathlib.Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    
    # Configure file handler
    file_handler = logging.FileHandler(log_file)
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
    if len(sys.argv) != 2:
        print('Usage: hblink.py config/config.json')
        print('Note: Copy config_sample.json to config.json and edit as needed')
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
