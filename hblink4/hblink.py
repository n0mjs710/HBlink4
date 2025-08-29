#!/usr/bin/env python3
"""
Copyright (c) 2025 by Cort Buffington, N0MJS

A complete architectural redesign of HBlink3, implementing         try:
            if _command == DMRD:
                self._handle_dmr_data(data, addr)
            elif _command == RPTL:
                self._handle_repeater_login(data[4:8], addr)
            elif _command == RPTK:
                self._handle_auth_response(data[4:8], data[8:], addr)
            elif _command == RPTC:
                if data[:5] == RPTCL:
                    self._handle_disconnect(data[5:9], addr)
                else:
                    self._handle_config(data, addr)
            elif _command == RPTPING:
                self._handle_ping(data[7:11], addr)
            elif _command == RPTSTAT:
                self._handle_status(data[4:8], data, addr)
            else:
                LOGGER.warning(f'Unknown command received from {addr[0]}:{addr[1]}: {_command}')ic
approach to DMR master services. The HomeBrew DMR protocol is UDP-based, used for 
communication between DMR repeaters and master servers.

License: GNU GPLv3
"""

import sys
import json
import logging
import pathlib
from typing import Dict, Any, Optional, Tuple, Union
from time import time
from random import randint
from hashlib import sha256

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
        RPTA, RPTL, RPTK, RPTC, RPTCL, RPTPING,
        DMRD, MSTNAK, MSTPONG, RPTACK
    )
    from .access_control import RepeaterMatcher
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, RPTPING,
        DMRD, MSTNAK, MSTPONG, RPTACK
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
    """UDP Implementation of HomeBrew DMR Master Protocol"""
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._repeaters: Dict[bytes, RepeaterState] = {}
        self._config = CONFIG
        self._matcher = RepeaterMatcher(CONFIG)
        self._timeout_task = None

    def startProtocol(self):
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
                LOGGER.warning(f'Repeater {radio_id.hex()} missed ping #{repeater.missed_pings}')
                
                if repeater.missed_pings >= max_missed:
                    LOGGER.error(f'Repeater {radio_id.hex()} timed out after {repeater.missed_pings} missed pings')
                    del self._repeaters[radio_id]

    def datagramReceived(self, data: bytes, addr: tuple):
        """Handle received UDP datagram"""
        ip, port = addr
        _command = data[:4]
        
        try:
            if _command == DMRD:
                self._handle_dmr_data(data, addr)
            elif _command == RPTL:
                self._handle_repeater_login(data[4:8], addr)
            elif _command == RPTK:
                self._handle_auth_response(data[4:8], data[8:], addr)
            elif _command == RPTC:
                if data[:5] == RPTCL:
                    self._handle_disconnect(data[5:9], addr)
                else:
                    self._handle_config(data, addr)
            elif _command == RPTPING:
                self._handle_ping(data[7:11], addr)
            else:
                LOGGER.warning(f'Unknown command received from {ip}:{port}: {_command}')
        except Exception as e:
            LOGGER.error(f'Error processing datagram from {ip}:{port}: {str(e)}')

    def _validate_repeater(self, radio_id: bytes, addr: PeerAddress) -> Optional[RepeaterState]:
        """Validate repeater state and address"""
        if radio_id not in self._repeaters:
            return None
            
        repeater = self._repeaters[radio_id]
        if repeater.sockaddr != addr:
            LOGGER.warning(f'Message from wrong IP for repeater {radio_id.hex()}')
            self._send_nak(radio_id, addr)
            return None
            
        return repeater

    def _handle_repeater_login(self, radio_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater login request"""
        ip, port = addr
        
        if radio_id in self._repeaters:
            repeater = self._repeaters[radio_id]
            if repeater.sockaddr != addr:
                LOGGER.warning(f'Repeater {radio_id.hex()} attempting to connect from {ip}:{port} but already connected from {repeater.ip}:{repeater.port}')
                self._send_nak(radio_id, addr)
                return
                
        # Create or update repeater state
        repeater = RepeaterState(radio_id=radio_id, ip=ip, port=port)
        self._repeaters[radio_id] = repeater
        
        # Send login ACK with salt
        salt_bytes = repeater.salt.to_bytes(4, 'big')
        self._send_packet(b''.join([RPTACK, salt_bytes]), addr)
        LOGGER.info(f'Repeater {radio_id.hex()} login request from {ip}:{port}, sent salt: {repeater.salt}')

    def _handle_auth_response(self, radio_id: bytes, auth_hash: bytes, addr: PeerAddress) -> None:
        """Handle authentication response from repeater"""
        repeater = self._validate_repeater(radio_id, addr)
        if not repeater:
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
                self._send_packet(b''.join([RPTACK, radio_id]), addr)
                LOGGER.info(f'Repeater {radio_id.hex()} authenticated successfully')
            else:
                LOGGER.warning(f'Repeater {radio_id.hex()} failed authentication')
                self._send_nak(radio_id, addr)
                del self._repeaters[radio_id]
                
        except Exception as e:
            LOGGER.error(f'Authentication error for repeater {radio_id.hex()}: {str(e)}')
            self._send_nak(radio_id, addr)
            del self._repeaters[radio_id]

    def _handle_config(self, data: bytes, addr: PeerAddress) -> None:
        """Handle configuration from repeater"""
        try:
            radio_id = data[4:8]
            repeater = self._validate_repeater(radio_id, addr)
            if not repeater or not repeater.authenticated:
                LOGGER.warning(f'Config from unauthenticated repeater {radio_id.hex()}')
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
            
            repeater.connected = True
            self._send_packet(b''.join([RPTACK, radio_id]), addr)
            LOGGER.info(f'Repeater {radio_id.hex()} ({repeater.callsign.decode().strip()}) configured successfully')
            
        except Exception as e:
            LOGGER.error(f'Error parsing config: {str(e)}')
            if 'radio_id' in locals():
                self._send_nak(radio_id, addr)

    def _handle_ping(self, radio_id: bytes, addr: PeerAddress) -> None:
        """Handle ping from repeater"""
        repeater = self._validate_repeater(radio_id, addr)
        if not repeater or not repeater.connected:
            LOGGER.warning(f'Ping from unconnected repeater {radio_id.hex()}')
            self._send_nak(radio_id, addr)
            return
            
        repeater.last_ping = time()
        repeater.ping_count += 1
        repeater.missed_pings = 0
        self._send_packet(b''.join([MSTPONG, radio_id]), addr)

    def _handle_disconnect(self, radio_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater disconnect"""
        repeater = self._validate_repeater(radio_id, addr)
        if repeater:
            LOGGER.info(f'Repeater {radio_id.hex()} ({repeater.callsign.decode().strip()}) disconnected')
            del self._repeaters[radio_id]
            
    def _handle_status(self, radio_id: bytes, data: bytes, addr: PeerAddress) -> None:
        """Handle repeater status report (including RSSI)"""
        repeater = self._validate_repeater(radio_id, addr)
        if repeater:
            # TODO: Parse and store RSSI and other status info
            LOGGER.debug(f'Status report from repeater {radio_id.hex()}: {data[8:].hex()}')
            self._send_packet(b''.join([RPTACK, radio_id]), addr)

    def _handle_dmr_data(self, data: bytes, addr: PeerAddress) -> None:
        """Handle DMR data"""
        if len(data) < 55:
            LOGGER.warning(f'Invalid DMR data packet from {addr[0]}:{addr[1]}')
            return
            
        radio_id = data[11:15]
        repeater = self._validate_repeater(radio_id, addr)
        if not repeater or not repeater.connected:
            LOGGER.warning(f'DMR data from unconnected repeater {radio_id.hex()}')
            return
            
        # Extract packet information
        _seq = data[4]
        _rf_src = data[5:8]
        _dst_id = data[8:11]
        _bits = data[15]
        _slot = 2 if (_bits & 0x80) else 1
        
        # TODO: Implement DMR data routing logic here
        # For now, just log it
        LOGGER.debug(f'DMR data from {radio_id.hex()}: seq={_seq}, src={_rf_src.hex()}, dst={_dst_id.hex()}, slot={_slot}')

    def _send_packet(self, data: bytes, addr: tuple):
        """Send packet to specified address"""
        self.transport.write(data, addr)

    def _send_nak(self, radio_id: bytes, addr: tuple):
        """Send NAK to specified address"""
        self._send_packet(b''.join([MSTNAK, radio_id]), addr)



from twisted.internet.task import LoopingCall

def setup_logging():
    """Configure logging"""
    log_level = getattr(logging, CONFIG['global']['log_level'])
    log_file = CONFIG['global']['log_file']
    log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    pathlib.Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(log_format)
    file_handler.setLevel(log_level)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    console_handler.setLevel(log_level)
    
    LOGGER.addHandler(file_handler)
    LOGGER.addHandler(console_handler)
    LOGGER.setLevel(log_level)

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

    # Listen on UDP port only since HomeBrew DMR only uses UDP
    reactor.listenUDP(
        CONFIG['global']['bind_port'],
        HBProtocol(),
        interface=CONFIG['global']['bind_ip']
    )
    
    LOGGER.info(f'HBlink4 master is running on {CONFIG["global"]["bind_ip"]}:{CONFIG["global"]["bind_port"]} (UDP)')
    reactor.run()

if __name__ == '__main__':
    main()
