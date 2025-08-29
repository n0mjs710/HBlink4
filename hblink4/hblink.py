#!/usr/bin/env python3
"""
HBlink4 - Next Generation DMR Master Server Protocol Handler
Copyright (c) 2025 by Cort Buffington, N0MJS

A complete architectural redesign of HBlink3, implementing a repeater-centric
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

# Constants for protocol commands
RPTA    = b'RPTA'
RPTL    = b'RPTL'
RPTK    = b'RPTK'
RPTC    = b'RPTC'
RPTCL   = b'RPTCL'
RPTPING = b'RPTPING'
DMRD    = b'DMRD'
MSTNAK  = b'MSTNAK'
MSTPONG = b'MSTPONG'
RPTACK  = b'RPTACK'

# Type definitions
RadioID = Union[bytes, str]
PeerAddress = Tuple[str, int]

def bhex(data: str) -> bytes:
    """Convert hex string to bytes"""
    return bytes.fromhex(data)

from dataclasses import dataclass, field

@dataclass
class RepeaterState:
    """Data class for storing repeater state"""
    radio_id: RadioID
    ip: str
    port: int
    connected: bool = False
    authenticated: bool = False
    last_ping: float = field(default_factory=time)
    ping_count: int = 0
    missed_pings: int = 0
    salt: int = field(default_factory=lambda: randint(0, 0xFFFFFFFF))
    
    # Metadata fields with defaults
    callsign: str = ""
    rx_freq: str = ""
    tx_freq: str = ""
    tx_power: str = ""
    colorcode: str = ""
    latitude: str = ""
    longitude: str = ""
    height: str = ""
    location: str = ""
    description: str = ""
    slots: str = ""
    url: str = ""
    software_id: str = ""
    package_id: str = ""
    
    @property
    def sockaddr(self) -> PeerAddress:
        """Get socket address tuple"""
        return (self.ip, self.port)

class HBProtocol(DatagramProtocol):
    """UDP Implementation of HomeBrew DMR Master Protocol"""
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._repeaters: Dict[RadioID, RepeaterState] = {}
        self._config = CONFIG

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

    def _validate_repeater(self, radio_id: RadioID, addr: PeerAddress) -> Optional[RepeaterState]:
        """Validate repeater state and address"""
        if radio_id not in self._repeaters:
            return None
            
        repeater = self._repeaters[radio_id]
        if repeater.sockaddr != addr:
            LOGGER.warning(f'Message from wrong IP for repeater {radio_id}')
            self._send_nak(radio_id, addr)
            return None
            
        return repeater

    def _normalize_radio_id(self, radio_id: RadioID) -> str:
        """Convert radio ID to consistent string format"""
        if isinstance(radio_id, bytes):
            return radio_id.hex().upper()
        return str(radio_id).upper()

    def _handle_repeater_login(self, radio_id: RadioID, addr: PeerAddress) -> None:
        """Handle repeater login request"""
        ip, port = addr
        normalized_id = self._normalize_radio_id(radio_id)
        
        if normalized_id in self._repeaters:
            repeater = self._repeaters[normalized_id]
            if repeater.sockaddr != addr:
                LOGGER.warning(f'Repeater {normalized_id} attempting to connect from {ip}:{port} but already connected from {repeater.ip}:{repeater.port}')
                self._send_nak(radio_id, addr)
                return
                
        # Create or update repeater state
        repeater = RepeaterState(radio_id=normalized_id, ip=ip, port=port)
        self._repeaters[normalized_id] = repeater
        
        # Send login ACK with salt
        salt_str = repeater.salt.to_bytes(4, 'big')
        self._send_packet(b''.join([RPTACK, salt_str]), addr)
        LOGGER.info(f'Repeater {normalized_id} login request from {ip}:{port}, sent salt: {repeater.salt}')

    def _handle_auth_response(self, radio_id: RadioID, auth_hash: bytes, addr: PeerAddress) -> None:
        """Handle authentication response from repeater"""
        normalized_id = self._normalize_radio_id(radio_id)
        repeater = self._validate_repeater(normalized_id, addr)
        if not repeater:
            return
            
        # Validate the hash
        salt_str = repeater.salt.to_bytes(4, 'big')
        calc_hash = bhex(sha256(b''.join([salt_str, self._config['passphrase'].encode()])).hexdigest())
        
        if auth_hash == calc_hash:
            repeater.authenticated = True
            self._send_packet(b''.join([RPTACK, radio_id if isinstance(radio_id, bytes) else radio_id.encode()]), addr)
            LOGGER.info(f'Repeater {normalized_id} authenticated successfully')
        else:
            LOGGER.warning(f'Repeater {normalized_id} failed authentication')
            self._send_nak(radio_id, addr)
            del self._repeaters[normalized_id]

    def _handle_config(self, data: bytes, addr: PeerAddress) -> None:
        """Handle configuration from repeater"""
        try:
            radio_id = self._normalize_radio_id(data[4:8])
            repeater = self._validate_repeater(radio_id, addr)
            if not repeater or not repeater.authenticated:
                LOGGER.warning(f'Config from unauthenticated repeater {radio_id}')
                self._send_nak(radio_id, addr)
                return
                
            # Parse configuration data
            repeater.callsign = data[8:16].decode().strip()
            repeater.rx_freq = data[16:25].decode().strip()
            repeater.tx_freq = data[25:34].decode().strip()
            repeater.tx_power = data[34:36].decode().strip()
            repeater.colorcode = data[36:38].decode().strip()
            repeater.latitude = data[38:46].decode().strip()
            repeater.longitude = data[46:55].decode().strip()
            repeater.height = data[55:58].decode().strip()
            repeater.location = data[58:78].decode().strip()
            repeater.description = data[78:97].decode().strip()
            repeater.slots = data[97:98].decode()
            repeater.url = data[98:222].decode().strip()
            repeater.software_id = data[222:262].decode().strip()
            repeater.package_id = data[262:302].decode().strip()
            
            repeater.connected = True
            self._send_packet(b''.join([RPTACK, radio_id.encode() if isinstance(radio_id, str) else radio_id]), addr)
            LOGGER.info(f'Repeater {radio_id} ({repeater.callsign}) configured successfully')
            
        except Exception as e:
            LOGGER.error(f'Error parsing config: {str(e)}')
            if 'radio_id' in locals():
                self._send_nak(radio_id, addr)

    def _handle_ping(self, radio_id: RadioID, addr: PeerAddress) -> None:
        """Handle ping from repeater"""
        normalized_id = self._normalize_radio_id(radio_id)
        repeater = self._validate_repeater(normalized_id, addr)
        if not repeater or not repeater.connected:
            LOGGER.warning(f'Ping from unconnected repeater {normalized_id}')
            self._send_nak(radio_id, addr)
            return
            
        repeater.last_ping = time()
        repeater.ping_count += 1
        repeater.missed_pings = 0
        self._send_packet(b''.join([MSTPONG, radio_id if isinstance(radio_id, bytes) else radio_id.encode()]), addr)

    def _handle_disconnect(self, radio_id: RadioID, addr: PeerAddress) -> None:
        """Handle repeater disconnect"""
        normalized_id = self._normalize_radio_id(radio_id)
        repeater = self._validate_repeater(normalized_id, addr)
        if repeater:
            LOGGER.info(f'Repeater {normalized_id} ({repeater.callsign}) disconnected')
            del self._repeaters[normalized_id]

    def _handle_dmr_data(self, data: bytes, addr: PeerAddress) -> None:
        """Handle DMR data"""
        if len(data) < 55:
            LOGGER.warning(f'Invalid DMR data packet from {addr[0]}:{addr[1]}')
            return
            
        radio_id = self._normalize_radio_id(data[11:15])
        repeater = self._validate_repeater(radio_id, addr)
        if not repeater or not repeater.connected:
            LOGGER.warning(f'DMR data from unconnected repeater {radio_id}')
            return
            
        # Extract packet information
        _seq = data[4]
        _rf_src = data[5:8]
        _dst_id = data[8:11]
        _bits = data[15]
        _slot = 2 if (_bits & 0x80) else 1
        
        # TODO: Implement DMR data routing logic here
        # For now, just log it
        LOGGER.debug(f'DMR data from {radio_id}: seq={_seq}, src={_rf_src.hex()}, dst={_dst_id.hex()}, slot={_slot}')

    def _send_packet(self, data: bytes, addr: tuple):
        """Send packet to specified address"""
        self.transport.write(data, addr)

    def _send_nak(self, radio_id: RadioID, addr: tuple):
        """Send NAK to specified address"""
        if isinstance(radio_id, str):
            radio_id = radio_id.encode()
        self._send_packet(b''.join([MSTNAK, radio_id]), addr)

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
        print('Usage: hblink.py <config_file>')
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
