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
import socket
from typing import Dict, Any, Optional, Tuple, Union, List, Set
from time import time
from random import randint
from hashlib import sha256, sha1
from hmac import new as hmac_new, compare_digest
import re

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
    from .utils import (
        safe_decode_bytes, normalize_addr, rid_to_int, bytes_to_int,
        cleanup_old_logs, setup_logging, PeerAddress, detect_connection_type,
        fmt_ts_tg
    )
    from .config import load_config as load_config_func, parse_outbound_connections as parse_outbound_func, parse_openbridge_connections as parse_openbridge_func
    from .protocol import (
        parse_dmr_packet, is_dmr_terminator, validate_packet_length,
        extract_packet_command, get_call_type_name, format_id_display,
        get_slot_name
    )
    from .models import (
        OutboundConnectionConfig, StreamState, OutboundState, RepeaterState,
        OpenBridgeConnectionConfig, OpenBridgeState
    )
    from .lc import (
        LC_OPT_GROUP_DEFAULT, LC_CARRIER_NONE, LC_CARRIER_VHEAD,
        LC_CARRIER_VTERM, LC_CARRIER_EMB, build_lc, synth_lc_base,
        decode_lc_from_vhead, encode_lc_forms, splice_full_lc, splice_emb_lc,
        classify_lc_carrier,
        STREAM_KIND_DATA, STREAM_KIND_VOICE, classify_stream_kind,
        dtype_name, decode_data_header,
    )
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from constants import (
        RPTA, RPTL, RPTK, RPTC, RPTCL, MSTCL, DMRD,
        MSTNAK, MSTPONG, RPTPING, RPTACK, RPTP, RPTO, DMRA
    )
    from access_control import RepeaterMatcher
    from events import EventEmitter
    from user_cache import UserCache
    from utils import (
        safe_decode_bytes, normalize_addr, rid_to_int, bytes_to_int,
        cleanup_old_logs, setup_logging, PeerAddress, detect_connection_type,
        fmt_ts_tg
    )
    from config import load_config as load_config_func, parse_outbound_connections as parse_outbound_func, parse_openbridge_connections as parse_openbridge_func
    from protocol import (
        parse_dmr_packet, is_dmr_terminator, validate_packet_length,
        extract_packet_command, get_call_type_name, format_id_display,
        get_slot_name
    )
    from models import (
        OutboundConnectionConfig, StreamState, OutboundState, RepeaterState,
        OpenBridgeConnectionConfig, OpenBridgeState
    )
    from lc import (
        LC_OPT_GROUP_DEFAULT, LC_CARRIER_NONE, LC_CARRIER_VHEAD,
        LC_CARRIER_VTERM, LC_CARRIER_EMB, build_lc, synth_lc_base,
        decode_lc_from_vhead, encode_lc_forms, splice_full_lc, splice_emb_lc,
        classify_lc_carrier,
        STREAM_KIND_DATA, STREAM_KIND_VOICE, classify_stream_kind,
        dtype_name, decode_data_header,
    )

# Data classes moved to models.py

class OutboundProtocol(asyncio.DatagramProtocol):
    """Protocol instance for a single outbound connection"""
    def __init__(self, hbprotocol: 'HBProtocol', connection_name: str):
        super().__init__()
        self.hbprotocol = hbprotocol
        self.connection_name = connection_name

    def datagram_received(self, data: bytes, addr: tuple):
        """Receive packet for this specific outbound connection"""
        self.hbprotocol._handle_outbound_packet(self.connection_name, data, addr)


class OpenBridgeProtocol(asyncio.DatagramProtocol):
    """Protocol instance for a single OpenBridge (OBP) trunk socket.

    Each OBP gets its own bound UDP socket. OBP has no login/keepalive
    handshake; authentication is the per-packet HMAC plus the source socket.
    """
    def __init__(self, hbprotocol: 'HBProtocol', obp_name: str):
        super().__init__()
        self.hbprotocol = hbprotocol
        self.obp_name = obp_name

    def datagram_received(self, data: bytes, addr: tuple):
        self.hbprotocol._handle_openbridge_packet(self.obp_name, data, addr)


class HBProtocol(asyncio.DatagramProtocol):
    """UDP Implementation of HomeBrew DMR Server Protocol"""
    def __init__(self, *args, **kwargs):
        super().__init__()
        # All inbound connections (repeaters, hotspots, network links) - see models.py terminology note
        self._repeaters: Dict[bytes, RepeaterState] = {}
        
        # Outbound connection state management (Phase 2)
        self._outbounds: Dict[str, 'OutboundState'] = {}  # keyed by connection name
        self._openbridges: Dict[str, 'OpenBridgeState'] = {}  # keyed by OBP name
        self._obp_by_tgid: Dict[bytes, str] = {}  # canonical 3-byte TGID -> owning OBP name (enabled only)
        self._outbound_by_id: Dict[bytes, str] = {}  # radio_id (4 bytes) -> name for packet routing by ID
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

        # Data-call log dedupe: one APRS beacon arrives as several DMR data
        # bursts (each its own HBP stream_id) within a few hundred ms. Coalesce
        # log output by (source, rf_src, dst_id, slot) so a single beacon =
        # one log line, not four.
        # Key: (source_key, rf_src, dst_id, slot), Value: timestamp of last log
        self._data_log_recent: Dict[tuple, float] = {}
        self._data_log_dedupe_window = 2.0
        
        # Initialize user cache (mandatory for proper operation)
        user_cache_config = CONFIG.get('global', {}).get('user_cache', {})
        cache_timeout = user_cache_config.get('timeout', 600)
        if cache_timeout < 60:
            LOGGER.warning(f'user_cache timeout of {cache_timeout}s is too low, using minimum of 60s')
            cache_timeout = 60
        self._user_cache = UserCache(timeout_seconds=cache_timeout)
        LOGGER.info(f'User cache initialized with {cache_timeout}s timeout')
        
        # No conversion caching - simple int.from_bytes() is fast enough
        # and avoids unbounded cache growth (memory leak prevention)
    
    # ========== ADDRESS VALIDATION METHODS ==========
    
    def _addr_matches(self, addr1: PeerAddress, addr2: PeerAddress) -> bool:
        """Compare two addresses, normalizing for IPv4/IPv6 differences"""
        return normalize_addr(addr1) == normalize_addr(addr2)
    
    def _addr_matches_repeater(self, repeater: RepeaterState, addr: PeerAddress) -> bool:
        """
        Optimized address comparison for repeater validation.
        RepeaterState.sockaddr is already normalized, so we only normalize the incoming address.
        """
        return repeater.sockaddr == normalize_addr(addr)
    

    
    def _format_tg_display(self, tg_set: Optional[set]) -> str:
        """Format TG set for human-readable display (logging)"""
        if tg_set is None:
            return 'All'
        elif not tg_set:
            return 'None'
        else:
            # Convert bytes back to integers for readable display
            return str(sorted(int.from_bytes(tg_bytes, 'big') for tg_bytes in tg_set))
    
    def _format_tg_json(self, tg_set: Optional[set]) -> Optional[list]:
        """Format TG set for JSON serialization (events)"""
        if tg_set is None:
            return None
        elif not tg_set:
            return []
        else:
            # Convert bytes back to integers for JSON (most efficient approach)
            return sorted(int.from_bytes(tg_bytes, 'big') for tg_bytes in tg_set)
    
    def _prepare_repeater_event_data(self, repeater_id: bytes, repeater: RepeaterState) -> dict:
        """
        Prepare common repeater data dictionary for event emission.
        Centralizes the logic for converting repeater state to JSON-serializable format.
        """
        translations_list = [
            [lslot, int.from_bytes(ltgid, 'big'),
             nslot, int.from_bytes(ntgid, 'big')]
            for (lslot, ltgid), (nslot, ntgid) in sorted(repeater.inbound_map.items())
        ]
        return {
            'repeater_id': rid_to_int(repeater_id),
            'callsign': repeater.get_callsign_str(),
            'location': repeater.get_location_str(),
            'address': f'{repeater.ip}:{repeater.port}',
            'rx_freq': repeater.get_rx_freq_str(),
            'tx_freq': repeater.get_tx_freq_str(),
            'colorcode': repeater.get_colorcode_str(),
            'connection_type': repeater.connection_type,
            'software_id': safe_decode_bytes(repeater.software_id),
            'package_id': safe_decode_bytes(repeater.package_id),
            'slot1_talkgroups': self._format_tg_json(repeater.slot1_talkgroups),
            'slot2_talkgroups': self._format_tg_json(repeater.slot2_talkgroups),
            'rpto_received': repeater.rpto_received,
            'translations': translations_list,
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
            rid_to_int(repeater_id),
            repeater.get_callsign_str()
        )
        
        # Convert config to internal representation:
        # None stays None (allow all), int lists become bytes sets for hot path performance
        if repeater_config.slot1_talkgroups is not None:
            repeater.slot1_talkgroups = {tg.to_bytes(3, 'big') for tg in repeater_config.slot1_talkgroups}
        else:
            repeater.slot1_talkgroups = None
            
        if repeater_config.slot2_talkgroups is not None:
            repeater.slot2_talkgroups = {tg.to_bytes(3, 'big') for tg in repeater_config.slot2_talkgroups}
        else:
            repeater.slot2_talkgroups = None

        # Seed unit-call participation from the pattern default. RPTO may
        # later override this with an explicit UNIT=true|false entry.
        repeater.unit_calls_enabled = repeater_config.default_unit_calls
        LOGGER.debug(
            f'Repeater {rid_to_int(repeater_id)} unit calls '
            f'{"ENABLED" if repeater.unit_calls_enabled else "DISABLED"} (pattern default)'
        )

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
                            # Convert strings directly to bytes for storage  
                            slot1_tgs.update(int(tg.strip()).to_bytes(3, 'big') for tg in tgs_str.split(',') if tg.strip())
                
                # Check for TS2=
                elif part.startswith('TS2='):
                    tgs_str = part[4:].strip()  # Everything after 'TS2='
                    if tgs_str == '*':
                        slot2_tgs = None  # Wildcard on TS2
                    else:
                        slot2_tgs = set()  # TS2 specified, start with empty
                        if tgs_str:
                            # Convert integers to bytes for storage
                            slot2_tgs.update(int(tg.strip()).to_bytes(3, 'big') for tg in tgs_str.split(',') if tg.strip())
        
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
                    # Create a connected UDP socket with our custom protocol that knows its connection name
                    transport, protocol = await loop.create_datagram_endpoint(
                        lambda: OutboundProtocol(self, config.name),
                        remote_addr=(ip, port)
                    )
                    LOGGER.info(f'[{config.name}] UDP endpoint created to {ip}:{port}')
                except Exception as e:
                    LOGGER.error(f'[{config.name}] Failed to create UDP endpoint: {e}')
                    
                    # Emit error event
                    self._events.emit('outbound_error', {
                        'connection_name': config.name,
                        'radio_id': config.radio_id,
                        'remote_address': config.address,
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
                self._outbound_by_id[config.radio_id.to_bytes(4, 'big')] = config.name
                
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
                    
                    # If not authenticated yet, retry RPTL
                    if not state.authenticated:
                        rptl_packet = RPTL + our_id_bytes
                        state.transport.sendto(rptl_packet)
                        LOGGER.debug(f'[{config.name}] Retrying RPTL (login) - no response yet')
                    else:
                        # Send RPTPING if authenticated
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
                                        'remote_address': config.address,
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
                    if state.transport and state.authenticated:
                        # Send RPTCL (disconnect) to cleanly close connection
                        try:
                            our_id_bytes = config.radio_id.to_bytes(4, 'big')
                            rptcl_packet = RPTCL + our_id_bytes
                            state.transport.sendto(rptcl_packet)
                            LOGGER.info(f'[{config.name}] Sent RPTCL (disconnect)')
                            await asyncio.sleep(0.1)  # Brief delay to let packet send
                        except Exception as e:
                            LOGGER.debug(f'[{config.name}] Error sending RPTCL: {e}')
                    if state.transport:
                        state.transport.close()
                    del self._outbounds[config.name]
                    LOGGER.info(f'[{config.name}] Cleaned up connection state')
            
            # Wait before reconnecting
            LOGGER.info(f'[{config.name}] Waiting {keepalive_interval}s before reconnect...')
            await asyncio.sleep(keepalive_interval)
    
    def _handle_outbound_packet(self, connection_name: str, data: bytes, addr: tuple):
        """
        Handle packets received from outbound server connections.
        Implements client-side HomeBrew protocol state machine.
        """
        ip = addr[0]
        port = addr[1]
        
        # Get outbound state by connection name (passed from OutboundProtocol)
        if connection_name not in self._outbounds:
            LOGGER.warning(f'Received packet for unknown outbound connection: {connection_name}')
            return
        
        state = self._outbounds[connection_name]
        
        # Check for commands - handle longer commands first
        _command = data[:4]
        if len(data) >= 7 and data[:7] == RPTPING:
            _command = RPTPING
        elif len(data) >= 7 and data[:7] == MSTPONG:
            _command = MSTPONG
        elif len(data) >= 6 and data[:6] == RPTACK:
            _command = RPTACK
        elif len(data) >= 6 and data[:6] == MSTNAK:
            _command = MSTNAK
        elif len(data) >= 5 and data[:5] == MSTCL:
            _command = MSTCL
        elif len(data) >= 5 and data[:5] == RPTCL:
            _command = RPTCL
        
        try:
            # RPTACK with salt - Challenge (response to RPTL)
            # HBlink4 server sends RPTACK + salt (not MSTCL) after receiving RPTL
            if _command == RPTACK and not state.auth_sent:
                if len(data) < 10:  # 6 bytes RPTACK + 4 bytes salt
                    LOGGER.error(f'[{connection_name}] Invalid RPTACK+salt packet length: {len(data)}')
                    return
                
                # Extract salt from challenge
                state.salt = int.from_bytes(data[6:10], 'big')
                LOGGER.info(f'[{connection_name}] Received RPTACK with salt: {state.salt}')
                
                # Send RPTK (auth response)
                our_id_bytes = state.config.radio_id.to_bytes(4, 'big')
                salt_bytes = state.salt.to_bytes(4, 'big')
                calc_hash = bytes.fromhex(
                    sha256(salt_bytes + state.config.passphrase.encode()).hexdigest()
                )
                rptk_packet = RPTK + our_id_bytes + calc_hash
                state.transport.sendto(rptk_packet)
                state.auth_sent = True  # Mark that we sent RPTK
                LOGGER.info(f'[{connection_name}] Sent RPTK (auth response)')
            
            # RPTACK - Acknowledgment (after sending RPTK/RPTC/RPTO)
            elif _command == RPTACK and state.auth_sent:
                if not state.config_sent:
                    # Config ACK (after RPTK)
                    state.config_sent = True
                    state.authenticated = True
                    LOGGER.info(f'[{connection_name}] Received RPTACK - Authentication successful')
                    
                    # Send RPTC (config)
                    self._send_outbound_config(state, (ip, port))
                elif not state.options_sent:
                    # Options/Config ACK
                    LOGGER.info(f'[{connection_name}] Received RPTACK - Config accepted')
                    
                    # Send RPTO (options) if configured
                    if state.config.options:
                        self._send_outbound_options(state, (ip, port))
                        state.options_sent = True
                    else:
                        state.options_sent = True
                        LOGGER.info(f'[{connection_name}] No options configured, connection complete')
                        
                        # Emit connection established event
                        self._events.emit('outbound_connected', {
                            'connection_name': connection_name,
                            'radio_id': state.config.radio_id,
                            'remote_address': state.config.address,  # Use original DNS name from config
                            'remote_port': state.port,
                            'slot1_talkgroups': self._format_tg_json(state.slot1_talkgroups),
                            'slot2_talkgroups': self._format_tg_json(state.slot2_talkgroups)
                        })
                else:
                    # Final ACK after RPTO
                    LOGGER.info(f'[{connection_name}] Received RPTACK - Options accepted, connection complete')
                    
                    # Emit connection established event
                    self._events.emit('outbound_connected', {
                        'connection_name': connection_name,
                        'radio_id': state.config.radio_id,
                        'remote_address': state.config.address,  # Use original DNS name from config
                        'remote_port': state.port,
                        'slot1_talkgroups': self._format_tg_json(state.slot1_talkgroups),
                        'slot2_talkgroups': self._format_tg_json(state.slot2_talkgroups)
                    })
            
            # MSTNAK - Negative Acknowledgment
            elif _command == MSTNAK:
                LOGGER.error(f'[{connection_name}] Received MSTNAK - Connection rejected by server')
                state.connected = False
                
                # Emit error event
                self._events.emit('outbound_error', {
                    'connection_name': connection_name,
                    'radio_id': state.config.radio_id,
                    'remote_address': state.config.address,
                    'remote_port': state.port,
                    'error_message': 'Connection rejected by server (MSTNAK)'
                })
            
            # MSTPONG - Keepalive response
            elif _command == MSTPONG:
                state.last_pong = time()
                state.missed_pongs = 0
                LOGGER.debug(f'[{connection_name}] Received MSTPONG')
            
            # MSTCL - Server disconnect
            elif _command[:5] == MSTCL:
                LOGGER.info(f'[{connection_name}] Received MSTCL - Server initiated disconnect')
                state.connected = False
                
                # Emit disconnection event
                self._events.emit('outbound_disconnected', {
                    'connection_name': connection_name,
                    'radio_id': state.config.radio_id,
                    'remote_address': state.config.address,
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
                LOGGER.warning(f'[{connection_name}] Unknown command from outbound server: {cmd_str}')
                
        except Exception as e:
            LOGGER.error(f'[{connection_name}] Error processing outbound packet: {e}')
    
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
        # Parse packet using unified parser
        packet = self._parse_dmr_packet(data)
        if not packet:
            LOGGER.warning(f'[{outbound_state.config.name}] Invalid DMRD packet length: {len(data)}')
            return
        
        # Extract fields from parsed packet
        _seq = packet['seq']
        _rf_src = packet['rf_src']
        _dst_id = packet['dst_id']
        _repeater_id = packet['repeater_id']  # Source repeater ID from remote server
        _slot = packet['slot']
        _call_type = packet['call_type']
        _frame_type = packet['frame_type']
        _stream_id = packet['stream_id']
        
        src_id = packet['src_id_int']
        remote_repeater_id = packet['repeater_id_int']
        _is_terminator = self._is_dmr_terminator(data, _frame_type)
        _dtype_vseq = data[15] & 0x0F
        _payload = data[20:53] if len(data) >= 53 else b''

        # Data calls (APRS, SMS, CSBK, etc.) are logged but never forwarded —
        # check before the unit-call dispatch so group and unit data both
        # land in the same drop path. Subsequent bursts in a multi-burst
        # data call re-install fresh StreamState but dedupe is applied in
        # _handle_data_stream so log output stays quiet.
        if classify_stream_kind(_frame_type, _dtype_vseq) == STREAM_KIND_DATA:
            conn_name = outbound_state.config.name
            dummy_id = outbound_state.config.radio_id.to_bytes(4, 'big')
            new_stream = self._handle_data_stream(
                source_key=f'outbound {conn_name}',
                owner_id=dummy_id,
                rf_src=_rf_src, dst_id=_dst_id, slot=_slot,
                stream_id=_stream_id, call_type_bit=_call_type,
                frame_type=_frame_type, dtype_vseq=_dtype_vseq,
                payload=_payload,
                cache_repeater_id=remote_repeater_id,
                cache_outbound_name=conn_name,
            )
            outbound_state.set_slot_stream(_slot, new_stream)
            emit_call_type = 'private' if _call_type == 1 else 'group'
            self._emit_stream_start(
                'outbound', conn_name, _slot, _rf_src, _dst_id, _stream_id,
                emit_call_type, False, remote_repeater_id,
                is_data=True,
            )
            return

        # Unit (private) calls from an outbound link take a dedicated path:
        # no TG ACL, no translation, RPF-style anti-loop (do not re-forward
        # to any outbound). The cache is populated with `outbound_name` so
        # any local user calling this subscriber routes back via this same
        # link — building an implicit reverse-path forwarding tree when
        # peers at both ends speak the same protocol.
        if _call_type == 1:
            self._handle_outbound_unit_call(
                data, outbound_state, packet, _is_terminator, remote_repeater_id
            )
            return

        # Check if this talkgroup is allowed on this outbound connection
        allowed_tgs = outbound_state.slot1_talkgroups if _slot == 1 else outbound_state.slot2_talkgroups
        
        # None = allow all, empty set = deny all, non-empty set = specific TGs
        if allowed_tgs is not None and (not allowed_tgs or _dst_id not in allowed_tgs):
            LOGGER.debug(f'[{outbound_state.config.name}] Dropping packet for unauthorized TG {packet["dst_id_int"]} on slot {_slot}')
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
            
            # Emit stream_start event for dashboard (RX stream from remote)
            self._emit_stream_start(
                'outbound',
                outbound_state.config.name,
                _slot,
                _rf_src,
                _dst_id,
                _stream_id,
                new_stream.call_type,
                False,  # Real RX stream
                remote_repeater_id  # Originating repeater ID from remote server
            )
            
            ts_tg = fmt_ts_tg(_slot, _dst_id)
            LOGGER.info(f'[{outbound_state.config.name}] RX stream started {ts_tg} '
                       f'src={src_id} from remote repeater {remote_repeater_id}')
        else:
            # Update existing stream
            current_stream.last_seen = current_time
            current_stream.packet_count += 1
        
        # Handle terminator
        if _is_terminator and current_stream:
            dummy_id = outbound_state.config.radio_id.to_bytes(4, 'big')
            self._end_stream(current_stream, dummy_id, _slot, current_time, 'terminator')
            
            # Emit stream_end event for dashboard (outbound RX termination)
            self._emit_stream_end(
                'outbound',
                outbound_state.config.name,
                _slot,
                current_stream,
                'terminator'
            )
        
        # Find local repeaters that should receive this traffic.
        # Source is an outbound connection → (_slot, _dst_id, _rf_src) already
        # network-side. Per-target, apply outbound_map (net → target-local)
        # and rewrite the embedded LC on VHEAD/VTERM/burst-B-E frames when
        # translation changes the addressing MMDVMHost would see.
        _dtype_vseq = data[15] & 0xF
        lc_carrier = classify_lc_carrier(_frame_type, _dtype_vseq)

        # Capture the stream's source-of-truth LC once (from VHEAD if this
        # frame is one, else synthesize). outbound-sourced streams use their
        # own StreamState on outbound_state to hold lc_base / lc_cache.
        rx_stream = outbound_state.get_slot_stream(_slot)
        if rx_stream is not None and rx_stream.lc_base is None:
            decoded_lc = None
            if lc_carrier == LC_CARRIER_VHEAD:
                decoded_lc = decode_lc_from_vhead(data[20:53])
            rx_stream.lc_base = decoded_lc or synth_lc_base(_dst_id, _rf_src)

        forwarded_count = 0
        for local_repeater_id, local_repeater in self._repeaters.items():
            # Only forward to connected repeaters
            if local_repeater.connection_state != 'connected':
                continue

            # ACL on network vocabulary
            if not self._check_outbound_routing(local_repeater_id, _slot, _dst_id):
                continue

            # Translate net → target-local for slot busy / packet rewrite
            if local_repeater.outbound_map:
                t_local = local_repeater.outbound_map.get((_slot, _dst_id))
                if t_local is not None:
                    out_slot, out_dst = t_local
                else:
                    out_slot, out_dst = _slot, _dst_id
            else:
                out_slot, out_dst = _slot, _dst_id

            # Check slot availability (don't hijack active streams) on target-local slot
            if self._is_slot_busy(local_repeater_id, out_slot, _stream_id, _rf_src, out_dst):
                continue

            # Addressing this target will see. LC needs rewriting only when
            # (out_dst, out_src) differs from what's already encoded by the
            # upstream sender AND the frame actually carries an LC.
            header_unchanged = (out_slot, out_dst) == (_slot, _dst_id)
            lc_needs_rewrite = (
                lc_carrier != LC_CARRIER_NONE
                and rx_stream is not None
                and rx_stream.lc_base is not None
                and out_dst != _dst_id
            )
            if header_unchanged and not lc_needs_rewrite:
                self._send_packet(data, local_repeater.sockaddr)
            else:
                buf = bytearray(data)
                if out_dst != _dst_id:
                    buf[8:11] = out_dst
                current_slot_bit = 2 if (buf[15] & 0x80) else 1
                if out_slot != current_slot_bit:
                    if out_slot == 2:
                        buf[15] |= 0x80
                    else:
                        buf[15] &= 0x7F
                if lc_needs_rewrite:
                    lc_cache = rx_stream.lc_cache
                    cache_key = (out_dst, _rf_src)
                    entry = lc_cache.get(cache_key)
                    if entry is None:
                        entry = encode_lc_forms(
                            build_lc(rx_stream.lc_base[0:3], out_dst, _rf_src)
                        )
                        lc_cache[cache_key] = entry
                    h_lc, t_lc, emb_lc = entry
                    payload = bytes(buf[20:53])
                    if lc_carrier == LC_CARRIER_VHEAD:
                        buf[20:53] = splice_full_lc(payload, h_lc)
                    elif lc_carrier == LC_CARRIER_VTERM:
                        buf[20:53] = splice_full_lc(payload, t_lc)
                    elif lc_carrier == LC_CARRIER_EMB:
                        buf[20:53] = splice_emb_lc(payload, emb_lc[_dtype_vseq])
                self._send_packet(bytes(buf), local_repeater.sockaddr)
            forwarded_count += 1

            # Track assumed stream state on local repeater using target-local values
            self._update_assumed_stream(local_repeater, out_slot, _rf_src, out_dst,
                                       _stream_id, _is_terminator, remote_repeater_id,
                                       net_slot=_slot, net_dst_id=_dst_id)
        
        # Log forwarding at DEBUG level
        if forwarded_count > 0:
            ts_tg = fmt_ts_tg(_slot, _dst_id)
            LOGGER.debug(f'[{outbound_state.config.name}] Forwarded DMRD '
                        f'{ts_tg} src={src_id} to {forwarded_count} local repeater(s)')


    def _handle_outbound_unit_call(self, data: bytes, outbound_state: 'OutboundState',
                                    packet: dict, is_terminator: bool,
                                    remote_repeater_id: int) -> None:
        """
        Handle a unit (private) call arriving on an outbound link.

        Flow mirrors `_handle_unit_stream_start` for local repeaters, but
        stream state is tracked on the outbound connection's slot and the
        user cache learns the source radio is reachable via this outbound.
        Targets are computed with `source_outbound_name` set, which causes
        `_calculate_unit_call_targets` to skip all outbound fanout
        (anti-loop; peers handle their own side of the RPF tree).
        """
        conn_name = outbound_state.config.name

        # Link-level gate: if this outbound isn't unit-enabled, drop.
        if not outbound_state.config.unit_calls_enabled:
            LOGGER.debug(
                f'[{conn_name}] Unit call dropped — unit_calls_enabled=False on this outbound'
            )
            return

        _rf_src = packet['rf_src']
        _dst_id = packet['dst_id']
        _slot = packet['slot']
        _stream_id = packet['stream_id']
        src_id = packet['src_id_int']
        dst_int = packet['dst_id_int']

        # Track RX stream state on the outbound slot. A new stream_id means
        # a new transmission — create fresh state, evict any active assumed
        # (TX) stream that happens to be on this slot (RX from the remote
        # wins, same rule as group calls).
        current_stream = outbound_state.get_slot_stream(_slot)
        current_time = time()

        if not current_stream or current_stream.stream_id != _stream_id:
            if current_stream and current_stream.is_assumed and not current_stream.ended:
                LOGGER.info(
                    f'[{conn_name}] TS{_slot} TX stream cleared by incoming unit-call RX stream'
                )
                outbound_state.set_slot_stream(_slot, None)
                self._active_calls -= 1

            # Figure out forwarding targets BEFORE creating the stream so we
            # can log target count alongside stream-start.
            target_repeaters, is_broadcast = self._calculate_unit_call_targets(
                source_repeater_id=None, slot=_slot,
                rf_src=_rf_src, dst_id=_dst_id, stream_id=_stream_id,
                source_outbound_name=conn_name,
            )

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
                call_type="private",
                is_assumed=False,  # Real RX from the remote
                is_unit_call=True,
                is_broadcast_unit_call=is_broadcast,
                target_repeaters=target_repeaters,
                routing_cached=True,
            )
            outbound_state.set_slot_stream(_slot, new_stream)

            # Dashboard event
            self._emit_stream_start(
                'outbound',
                conn_name,
                _slot,
                _rf_src,
                _dst_id,
                _stream_id,
                'private',
                False,
                remote_repeater_id,
            )

            mode_tag = '[broadcast]' if is_broadcast else (
                f'[one-to-one via {int.from_bytes(next(iter(target_repeaters)), "big")}]'
                if target_repeaters else '[no eligible targets]'
            )
            LOGGER.info(
                f'[{conn_name}] Unit RX stream started TS/RID: {_slot}/{dst_int} '
                f'src={src_id} targets={len(target_repeaters)} '
                f'stream_id={_stream_id.hex()} {mode_tag}'
            )

            # Cache the source radio as reachable via this outbound so local
            # users calling it route back over this same link.
            if self._user_cache:
                self._user_cache.update(
                    radio_id=src_id,
                    repeater_id=0,
                    callsign='',
                    slot=_slot,
                    talkgroup=dst_int,
                    outbound_name=conn_name,
                )
        else:
            current_stream.last_seen = current_time
            current_stream.packet_count += 1

        # Forward to each target. Unit calls never translate, so the packet
        # goes out as-is. We still rewrite the repeater-id header field to
        # the target repeater's id (existing downstream code expects it).
        source_stream = outbound_state.get_slot_stream(_slot)
        if source_stream is None:
            return

        for target in source_stream.target_repeaters or ():
            if isinstance(target, tuple):
                # Defensive: _calculate_unit_call_targets with a source
                # outbound should never return outbound targets, but skip
                # any that slip through.
                continue
            target_repeater_id = target
            target_repeater = self._repeaters.get(target_repeater_id)
            if not target_repeater:
                continue
            # No translation for unit calls — just forward the packet.
            self._send_packet(data, target_repeater.sockaddr)
            self._update_assumed_stream(
                target_repeater, _slot, _rf_src, _dst_id, _stream_id,
                is_terminator, remote_repeater_id,
                is_unit_call=True,
            )

        # Handle terminator on the outbound side
        if is_terminator and source_stream:
            dummy_id = outbound_state.config.radio_id.to_bytes(4, 'big')
            self._end_stream(source_stream, dummy_id, _slot, current_time, 'terminator')
            self._emit_stream_end(
                'outbound', conn_name, _slot, source_stream, 'terminator'
            )


    # ========== END HELPER METHODS ==========
        
    def cleanup(self) -> None:
        """Send disconnect messages to all repeaters and cleanup resources."""
        LOGGER.info("Starting graceful shutdown...")
        
        # Send MSTCL to all connected repeaters
        if self._port:  # Only attempt to send if we have a port
            for repeater_id, repeater in self._repeaters.items():
                if repeater.connection_state == 'connected':
                    try:
                        LOGGER.info(f"Sending disconnect to repeater {rid_to_int(repeater_id)}")
                        # asyncio uses sendto() instead of write(data, addr)
                        self._port.sendto(MSTCL, repeater.sockaddr)
                    except Exception as e:
                        LOGGER.error(f"Error sending disconnect to repeater {rid_to_int(repeater_id)}: {e}")
        
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
                        'remote_address': outbound.config.address,
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
                LOGGER.warning(f'Repeater {rid_to_int(repeater_id)} missed ping #{repeater.missed_pings}')
                
                # Emit event to update dashboard with missed ping count
                self._events.emit('repeater_connected', self._prepare_repeater_event_data(repeater_id, repeater))
                
                if repeater.missed_pings >= max_missed:
                    LOGGER.error(f'Repeater {rid_to_int(repeater_id)} timed out after {repeater.missed_pings} missed pings')
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
            reason_text = f'reason=timeout - entering hang time ({hang_time}s)'
        
        # Log stream end (DEBUG for TX, INFO for RX). Match stream-start
        # format. Group calls get net/rf translation annotation via fmt_ts_tg;
        # unit (private) calls carry a subscriber ID in dst_id rather than a
        # TGID, and never translate — format them as UNIT explicitly so the
        # dst isn't misread as a talkgroup. For outbound-sourced streams
        # repeater_id is a synthetic dummy that won't be in self._repeaters —
        # we still log, just without translation annotation.
        rid_int = rid_to_int(repeater_id)
        src_int = int.from_bytes(stream.rf_src, "big")
        dst_int = int.from_bytes(stream.dst_id, "big")
        # Data streams already logged once at dedupe time by _handle_data_stream;
        # quiet their end line so a busy APRS channel doesn't echo through here.
        log = (LOGGER.debug if stream_type == "TX" or stream.call_type == "data"
               else LOGGER.info)

        if stream.is_unit_call:
            call_type_prefix = "Unit"
            ts_addr = f'TS/RID: {slot}/{dst_int}'
        else:
            call_type_prefix = "Group"
            repeater = self._repeaters.get(repeater_id)
            if repeater and repeater.inbound_map:
                net_slot, net_dst_id = repeater.inbound_map.get(
                    (slot, stream.dst_id), (slot, stream.dst_id)
                )
            else:
                net_slot, net_dst_id = slot, stream.dst_id
            ts_addr = fmt_ts_tg(net_slot, net_dst_id, slot, stream.dst_id)

        log(f'{call_type_prefix} {stream_type} stream ended on repeater {rid_int} {ts_addr} '
            f'src={src_int} duration={duration:.2f}s '
            f'packets={stream.packet_count} {reason_text}')
        
        # Emit stream_end event for repeater card display
        # Dashboard will filter TX streams (is_assumed=True) from Recent Events log
        self._emit_stream_end(
            'repeater',
            rid_int,
            slot,
            stream,
            end_reason
        )
        
        # Decrement active calls counter if this was an assumed (TX) stream
        if stream.is_assumed:
            self._active_calls -= 1

    # ================================
    # Stream Helper Functions  
    # ================================
    
    def _emit_stream_start(self, connection_type: str, connection_id: str,
                          slot: int, src_id: bytes, dst_id: bytes, stream_id: bytes,
                          call_type: str, is_assumed: bool = False,
                          remote_repeater_id: int = None,
                          is_data: bool = False) -> None:
        """
        Stream_start event emission for all connection types.

        Args:
            connection_type: 'repeater' or 'outbound'
            connection_id: repeater_id (int) or connection_name (str)
            slot: Slot number
            src_id: Source DMR ID (bytes)
            dst_id: Destination ID (bytes)
            stream_id: Stream ID (bytes)
            call_type: Call type string — addressing dimension only ('group'
                or 'private'). Payload kind (voice vs data) lives in is_data.
            is_assumed: Whether this is an assumed (TX) stream
            remote_repeater_id: For outbound connections, the originating repeater ID
            is_data: True for data calls (APRS/SMS/CSBK/etc.). Kept orthogonal
                to call_type so dashboards can render both dimensions (group
                vs unit AND voice vs data) without encoding them in one string.
        """
        event_data = {
            'slot': slot,
            'src_id': int.from_bytes(src_id, 'big'),
            'dst_id': int.from_bytes(dst_id, 'big'),
            'stream_id': stream_id.hex(),
            'call_type': call_type,
            'is_assumed': is_assumed,
            'is_data': is_data,
        }
        
        if connection_type == 'repeater':
            event_data['repeater_id'] = int(connection_id) if isinstance(connection_id, str) else connection_id
        else:  # outbound 
            event_data['connection_type'] = connection_type
            event_data['connection_name'] = connection_id
            if remote_repeater_id is not None:
                event_data['remote_repeater_id'] = remote_repeater_id
            
        self._events.emit('stream_start', event_data)
    
    def _emit_stream_end(self, connection_type: str, connection_id: str,
                        slot: int, stream: StreamState, end_reason: str) -> None:
        """
        Stream_end event emission for all connection types.
        
        Args:
            connection_type: 'repeater' or 'outbound'
            connection_id: repeater_id (int) or connection_name (str)
            slot: Slot number
            stream: StreamState object
            end_reason: Reason for ending
        """
        duration = time() - stream.start_time
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)

        # Split the StreamState.call_type (server-internal, uses 'data' as a
        # flag value) back into the wire-format dimensions the dashboard
        # expects: call_type='group'|'private' for addressing, is_data for
        # payload kind. is_unit_call on the state tells us the addressing.
        if stream.call_type == 'data':
            emit_call_type = 'private' if stream.is_unit_call else 'group'
            emit_is_data = True
        else:
            emit_call_type = stream.call_type
            emit_is_data = False

        event_data = {
            'slot': slot,
            'src_id': int.from_bytes(stream.rf_src, 'big'),
            'dst_id': int.from_bytes(stream.dst_id, 'big'),
            'stream_id': stream.stream_id.hex(),
            'duration': round(duration, 2),
            'packet_count': stream.packet_count,
            'end_reason': end_reason,
            'hang_time': hang_time,
            'call_type': emit_call_type,
            'is_assumed': stream.is_assumed,
            'is_data': emit_is_data,
        }
        
        if connection_type == 'repeater':
            event_data['repeater_id'] = int(connection_id) if isinstance(connection_id, str) else connection_id
        else:  # outbound
            event_data['connection_type'] = connection_type
            event_data['connection_name'] = connection_id
            
        self._events.emit('stream_end', event_data)

    # ========== TIMEOUT & MAINTENANCE METHODS ==========

    def _check_timeout(self, connection_type: str, connection_id: str,
                      slot: int, stream: StreamState, current_time: float,
                      stream_timeout: float, hang_time: float,
                      synthetic_repeater_id: bytes = None) -> bool:
        """
        Timeout checking for all connection types.
        
        Args:
            connection_type: 'repeater' or 'outbound'
            connection_id: repeater_id (int) or connection_name (str)
            slot: Slot number
            stream: StreamState object
            current_time: Current timestamp
            stream_timeout: Stream timeout in seconds
            hang_time: Hang time in seconds
            synthetic_repeater_id: For outbound connections, synthetic ID for _end_stream
            
        Returns:
            True if slot should be cleared, False otherwise
        """
        if not stream.is_active(stream_timeout):
            if not stream.ended:
                # Stream just ended - use unified ending logic
                if connection_type == 'repeater':
                    # For repeaters, connection_id is the repeater_id as bytes
                    rid_bytes = connection_id if isinstance(connection_id, bytes) else int(connection_id).to_bytes(4, 'big')
                else:
                    # For outbound, use synthetic repeater_id 
                    rid_bytes = synthetic_repeater_id
                    
                self._end_stream(stream, rid_bytes, slot, current_time, 'timeout')
                return False  # Don't clear yet - entering hang time
                
            elif not stream.is_in_hang_time(stream_timeout, hang_time):
                # Hang time expired - clear the slot
                hang_duration = current_time - stream.end_time if stream.end_time else 0
                stream_type = "TX" if stream.is_assumed else "RX"
                
                # Log with appropriate connection identifier
                if connection_type == 'repeater':
                    conn_display = f"repeater {connection_id}"
                else:
                    conn_display = f"outbound {connection_id}"
                    
                LOGGER.debug(f'{stream_type} hang time completed on {conn_display} slot {slot}: '
                           f'src={int.from_bytes(stream.rf_src, "big")}, '
                           f'dst={int.from_bytes(stream.dst_id, "big")}, '
                           f'hang_duration={hang_duration:.2f}s')
                
                # Emit hang_time_expired event with appropriate format
                if connection_type == 'repeater':
                    event_data = {
                        'repeater_id': int(connection_id) if isinstance(connection_id, str) else connection_id,
                        'slot': slot
                    }
                else:  # outbound
                    event_data = {
                        'connection_type': connection_type,
                        'connection_name': connection_id,
                        'slot': slot
                    }
                    
                self._events.emit('hang_time_expired', event_data)
                return True  # Clear the slot
        
        return False  # Stream still active or in hang time
    
    def _check_slot_timeout(self, repeater_id: bytes, repeater: RepeaterState, slot: int, 
                           stream: StreamState, current_time: float, stream_timeout: float, 
                           hang_time: float) -> bool:
        """
        Check and handle timeout for a single slot stream.
        
        Returns:
            True if slot should be cleared, False otherwise
        """
        return self._check_timeout(
            'repeater',
            rid_to_int(repeater_id),
            slot,
            stream,
            current_time,
            stream_timeout,
            hang_time,
            repeater_id  # Pass as synthetic_repeater_id for consistency
        )

    def _check_outbound_slot_timeout(self, conn_name: str, outbound: OutboundState, slot: int,
                                   stream: StreamState, current_time: float, stream_timeout: float,
                                   hang_time: float) -> bool:
        """
        Check and handle timeout for a single outbound slot stream.
        
        Returns:
            True if slot should be cleared, False otherwise
        """
        return self._check_timeout(
            'outbound',
            conn_name,
            slot,
            stream,
            current_time,
            stream_timeout,
            hang_time,
            outbound.config.radio_id.to_bytes(4, 'big')  # synthetic_repeater_id
        )
    
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
        
        # Check outbound connections for hang time expiration
        for conn_name, outbound in self._outbounds.items():
            if not outbound.authenticated:
                continue
                
            # Check slot 1
            if outbound.slot1_stream:
                if self._check_outbound_slot_timeout(conn_name, outbound, 1, outbound.slot1_stream,
                                                   current_time, stream_timeout, hang_time):
                    outbound.slot1_stream = None
            
            # Check slot 2
            if outbound.slot2_stream:
                if self._check_outbound_slot_timeout(conn_name, outbound, 2, outbound.slot2_stream,
                                                   current_time, stream_timeout, hang_time):
                    outbound.slot2_stream = None

        # Reap stale OpenBridge streams. OBP is stream-multiplexed (no slot to
        # protect and no hang time), so a stream is simply dropped once it ends
        # or goes quiet past the stream timeout (backstop for a lost terminator).
        for obp in self._openbridges.values():
            if not obp.streams:
                continue
            stale = [(sid, s) for sid, s in obp.streams.items()
                     if s.ended or (current_time - s.last_seen) > stream_timeout]
            for sid, s in stale:
                # If the terminator was lost, the stream_end was never emitted, so
                # the dashboard pill would hang. Emit it now (RX or TX alike — both
                # live in this dict). Streams already flagged ended emitted their
                # stream_end at the terminator; don't double-emit those.
                if not s.ended:
                    s.ended = True
                    s.end_time = current_time
                    self._emit_stream_end('openbridge', obp.config.name, s.slot, s, 'timeout')
                del obp.streams[sid]

        # Cleanup old denied stream entries (older than 10 seconds)
        denied_cutoff = current_time - 10.0
        self._denied_streams = {k: v for k, v in self._denied_streams.items() if v > denied_cutoff}

        # Cleanup stale data-call log-dedupe entries
        data_log_cutoff = current_time - (self._data_log_dedupe_window * 2)
        self._data_log_recent = {
            k: v for k, v in self._data_log_recent.items() if v > data_log_cutoff
        }
    
    def _cleanup_user_cache(self):
        """Periodic cleanup of expired user cache entries"""
        if self._user_cache:
            removed = self._user_cache.cleanup()
            if removed > 0:
                LOGGER.debug(f'User cache cleanup: removed {removed} expired entries')
    

    def _send_initial_state(self):
        """Send current state of all connected repeaters and outbound connections to dashboard (called on reconnect)"""
        try:
            # Send all connected repeaters
            for repeater_id, repeater in self._repeaters.items():
                if repeater.connected and repeater.connection_state == 'connected':
                    # Emit repeater_connected for each already-connected repeater
                    self._events.emit('repeater_connected', self._prepare_repeater_event_data(repeater_id, repeater))
            
            # Send all outbound connections (with their current status)
            for conn_name, outbound in self._outbounds.items():
                status = 'connecting'  # Default status
                if outbound.authenticated and outbound.config_sent:
                    status = 'connected'
                elif not outbound.connected:
                    status = 'disconnected'
                
                event_type = f'outbound_{status}'
                self._events.emit(event_type, {
                    'connection_name': conn_name,
                    'radio_id': outbound.config.radio_id,
                    'remote_address': outbound.config.address,
                    'remote_port': outbound.port,
                    'slot1_talkgroups': self._format_tg_json(outbound.slot1_talkgroups),
                    'slot2_talkgroups': self._format_tg_json(outbound.slot2_talkgroups)
                })
            
            # Send all OpenBridge trunks (bound = up; OBP has no auth handshake)
            for obp in self._openbridges.values():
                self._events.emit('openbridge_connected', self._openbridge_event_data(obp))

            LOGGER.info(f'📤 Sent initial state: {len([r for r in self._repeaters.values() if r.connected])} connected repeaters, {len(self._outbounds)} outbound connections, {len(self._openbridges)} OpenBridge trunks')
        except Exception as e:
            LOGGER.error(f'Error sending initial state: {e}')
    
    def _check_inbound_routing(self, repeater_id: bytes, slot: int, dst_id: bytes) -> bool:
        """
        Check if a repeater is allowed to send traffic on this TS/TGID.

        Uses cached TG bytes sets in RepeaterState for O(1) lookup with no conversion.
        If the repeater has an inbound translation map, the (slot,dst_id) are
        translated to network-side values before the ACL check — subscription
        sets are always kept in network-side vocabulary.

        Args:
            repeater_id: Repeater ID to check
            slot: Timeslot (1 or 2) — as received from the repeater (local)
            dst_id: Destination TGID as 3-byte DMR format — as received (local)

        Returns:
            True if traffic is allowed, False otherwise
        """
        # Get repeater state
        repeater = self._repeaters.get(repeater_id)
        if not repeater:
            return False

        # Translate local→network before ACL so subscription set (network-side)
        # and the packet's addressing line up.
        if repeater.inbound_map:
            translated = repeater.inbound_map.get((slot, dst_id))
            if translated is not None:
                slot, dst_id = translated
            elif (slot, dst_id) in repeater.outbound_map:
                # Repeater keyed the net-side address for a TG it declared a
                # local alias for. The subscription set carries the net-side
                # TGID (needed for forwarding TO this repeater), so without
                # this guard the packet would sneak past ACL on the net-side
                # key instead of the declared local one.
                return False

        # Get slot-specific talkgroup set from repeater state
        allowed_tgids = repeater.slot1_talkgroups if slot == 1 else repeater.slot2_talkgroups
        
        # None means no restrictions (allow all)
        if allowed_tgids is None:
            return True
        
        # Empty set means deny all
        if not allowed_tgids:
            return False
        
        # O(1) set membership check with no bytes→int conversion!
        return dst_id in allowed_tgids
    
    def _check_outbound_routing(self, repeater_id: bytes, slot: int, dst_id: bytes) -> bool:
        """
        Check if traffic should be forwarded to this repeater on this TS/TGID.
        
        Uses cached TG bytes sets in RepeaterState for O(1) lookup with no conversion.
        Same set and logic as inbound - symmetric routing.
        
        Args:
            repeater_id: Repeater ID to check
            slot: Timeslot (1 or 2)  
            dst_id: Destination TGID as 3-byte DMR format
            
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
        
        # O(1) set membership check with no bytes→int conversion!
        return dst_id in allowed_tgids
    
    def _is_slot_busy(self, repeater_id: bytes, slot: int, stream_id: bytes,
                     rf_src: bytes = None, dst_id: bytes = None,
                     is_unit_call: bool = False) -> bool:
        """
        Check if a slot is busy with a different stream (contention check).

        Hang-time semantics depend on call type:
          - Group call: slot is reserved for the TALKGROUP conversation — any
            user on the same TG may continue, OR the original source may
            switch to a different TG (fast TG switch). Anything else is a
            hijack and the slot reads busy.
          - Unit call: slot is reserved for the SUBSCRIBER PAIR — either
            direction of the same A↔B pair may continue, OR the original
            source may start a new unit call to a different target.
            Anything else is a hijack and the slot reads busy.
        These semantics are applied only when the current stream matches the
        incoming call's type. Cross-type (group-in-hangtime + incoming unit,
        or vice versa) always reads busy — we don't mix the two vocabularies.

        Args:
            repeater_id: Repeater ID to check
            slot: Timeslot to check
            stream_id: Current stream ID (to allow same stream through)
            rf_src: Source subscriber ID (optional, for hang time check)
            dst_id: Destination TGID or target radio ID (optional, for hang time check)
            is_unit_call: True when the incoming call is a unit (private) call

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

            # Still in hang time — apply the appropriate hijack rules.
            if rf_src and dst_id:
                if is_unit_call and current_stream.is_unit_call:
                    # Subscriber-pair semantics: either direction of same pair,
                    # or same source calling a different target, passes through.
                    same_pair = (
                        (current_stream.rf_src == rf_src and current_stream.dst_id == dst_id)
                        or (current_stream.rf_src == dst_id and current_stream.dst_id == rf_src)
                    )
                    same_src = (current_stream.rf_src == rf_src)
                    if same_pair or same_src:
                        return False
                elif not is_unit_call and not current_stream.is_unit_call:
                    # Group-call hang time: protect the TG conversation.
                    if current_stream.rf_src == rf_src:
                        return False  # Same user, allow through (TG switch)
                    if current_stream.dst_id == dst_id:
                        return False  # Same TG, different user — allow
                # Cross-type or mismatch — fall through, slot reads busy.

        # Slot is busy with a different active stream or protected by hang time
        return True

    def datagram_received(self, data: bytes, addr: tuple):
        """Handle received UDP datagram (for inbound repeater connections only)"""
        # Handle both IPv4 (ip, port) and IPv6 (ip, port, flowinfo, scopeid) address formats
        ip = addr[0]
        port = addr[1]
        
        # Note: Outbound connections have their own protocol instances (OutboundProtocol)
        # so they never hit this method - this is ONLY for inbound repeater connections
        
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
                
            if not repeater_id:
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

            # Per-packet logging - only enable for heavy troubleshooting
            #LOGGER.debug(f'Packet received: cmd={_command}, repeater_id={rid_to_int(repeater_id)}, addr={addr}')

            # Get repeater state once (for both NAK check and ping update)
            repeater = self._repeaters.get(repeater_id)
            
            # If repeater is not registered and this is not a login or auth packet, send NAK and return
            if not repeater and _command not in [RPTL, RPTK]:
                self._send_nak(repeater_id, addr, reason="Repeater not registered")
                return

            # Update ping time for connected repeaters
            if repeater and repeater.connection_state == 'connected':
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
            #LOGGER.debug(f'Repeater {rid_to_int(repeater_id)} not found in _repeaters dict')
            self._send_nak(repeater_id, addr, reason="Repeater not registered")
            return None
            
        repeater = self._repeaters[repeater_id]
        # Per-packet logging - only enable for heavy troubleshooting
        #LOGGER.debug(f'Validating repeater {rid_to_int(repeater_id)}: state="{repeater.connection_state}", stored_addr={repeater.sockaddr}, incoming_addr={addr}')
        
        if not self._addr_matches_repeater(repeater, addr):
            LOGGER.warning(f'Message from wrong IP for repeater {rid_to_int(repeater_id)}')
            self._send_nak(repeater_id, addr, reason="Message from incorrect IP address")
            return None
            
        return repeater
    
    def _handle_data_stream(self, source_key: str, owner_id: bytes,
                            rf_src: bytes, dst_id: bytes, slot: int,
                            stream_id: bytes, call_type_bit: int,
                            frame_type: int, dtype_vseq: int,
                            payload: bytes,
                            cache_repeater_id: int = 0,
                            cache_outbound_name: Optional[str] = None) -> StreamState:
        """
        Track a data call: log it, optionally decode the data header, emit a
        dashboard event — but DO NOT forward. Same path for group and unit
        data; the only forwarding-relevant distinction is that `dst_id` is
        a TGID for group data and a target RID for unit data.

        Returns a StreamState marked with call_type="data". Caller must
        install it on the owning repeater/outbound slot so subsequent
        bursts land on the same state instead of churning through
        fast-terminator + contention logic.

        Log output is deduped per (source_key, rf_src, dst_id, slot) inside
        _data_log_dedupe_window seconds so a multi-burst APRS beacon emits
        one log line, not four.
        """
        current_time = time()
        src_int = bytes_to_int(rf_src)
        dst_int = bytes_to_int(dst_id)
        is_group = (call_type_bit == 0)

        dedupe_key = (source_key, rf_src, dst_id, slot)
        last_seen = self._data_log_recent.get(dedupe_key)
        emit_log = (last_seen is None
                    or (current_time - last_seen) > self._data_log_dedupe_window)

        kind_tag = 'Group' if is_group else 'Unit'
        dst_label = 'TGID' if is_group else 'RID'

        # Always attempt Tier-2 decode on Data Header frames so operators see
        # the DPF / SAP in the log. CSBK / rate-½/rate-¾ / rate-1 bursts carry
        # transport payload we can't decode without protocol context, so we
        # just tag them with the frame type name and move on.
        decoded = None
        if frame_type == 2 and dtype_vseq == 6 and len(payload) >= 33:
            decoded = decode_data_header(payload[:33])

        if emit_log:
            self._data_log_recent[dedupe_key] = current_time
            dname = dtype_name(dtype_vseq) if frame_type == 2 else f'voice-ft{frame_type}'
            parts = [
                f'{kind_tag} data call on {source_key} TS/{dst_label}: {slot}/{dst_int}',
                f'src={src_int}',
                f'dtype={dname}',
                f'stream_id={stream_id.hex()}',
            ]
            if decoded is not None:
                parts.append(f'dpf={decoded["dpf_name"]}')
                parts.append(f'sap={decoded["sap_name"]}')
                parts.append(f'blocks={decoded["blocks_to_follow"]}')
                parts.append(f'raw={decoded["raw"].hex()}')
            else:
                parts.append(f'payload={payload[:16].hex()}')
            parts.append('[not forwarded]')
            LOGGER.info(' '.join(parts))

        new_stream = StreamState(
            repeater_id=owner_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            start_time=current_time,
            last_seen=current_time,
            stream_id=stream_id,
            packet_count=1,
            call_type="data",
            is_unit_call=(not is_group),
            # Data calls do not fan out, so nothing to cache.
            target_repeaters=set(),
            routing_cached=True,
        )

        # Populate the user cache — data calls are as good a locator as
        # voice. If a radio beacons APRS from repeater X now, a unit voice
        # call placed to that radio ten minutes later routes to X instead
        # of broadcasting.
        if self._user_cache:
            self._user_cache.update(
                radio_id=src_int,
                repeater_id=cache_repeater_id,
                callsign='',
                slot=slot,
                talkgroup=dst_int,
                outbound_name=cache_outbound_name,
            )

        return new_stream

    def _handle_stream_start(self, repeater: RepeaterState, rf_src: bytes, dst_id: bytes,
                             slot: int, stream_id: bytes, call_type_bit: int = 1,
                             frame_type: int = 0, dtype_vseq: int = 0,
                             payload: bytes = b'') -> bool:
        """
        Handle the start of a new stream on a repeater slot.
        Returns True if the stream can proceed, False if there's a contention.
        """
        # Voice-vs-data branch: the HBP call_type bit is only group-vs-unit,
        # so check the payload frame_type/dtype_vseq to tell data bursts
        # (APRS, SMS, GPS, CSBK) from real voice. Data calls are logged but
        # never forwarded.
        if classify_stream_kind(frame_type, dtype_vseq) == STREAM_KIND_DATA:
            rid_int = rid_to_int(repeater.repeater_id)
            new_stream = self._handle_data_stream(
                source_key=f'repeater {rid_int}',
                owner_id=repeater.repeater_id,
                rf_src=rf_src, dst_id=dst_id, slot=slot,
                stream_id=stream_id, call_type_bit=call_type_bit,
                frame_type=frame_type, dtype_vseq=dtype_vseq,
                payload=payload,
                cache_repeater_id=rid_int,
                cache_outbound_name=None,
            )
            repeater.set_slot_stream(slot, new_stream)
            emit_call_type = 'private' if call_type_bit == 1 else 'group'
            self._emit_stream_start(
                'repeater', rid_int, slot, rf_src, dst_id, stream_id,
                emit_call_type, False, is_data=True,
            )
            return True

        # Unit (private) calls take a dedicated path: separate routing (user
        # cache lookup with broadcast fallback), subscriber-pair hang-time
        # semantics, no TG access control, no DMRD translation.
        if call_type_bit == 1:
            return self._handle_unit_stream_start(repeater, rf_src, dst_id, slot, stream_id)
        
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
                LOGGER.info(f'Repeater {rid_to_int(repeater.repeater_id)} slot {slot} '
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
                            LOGGER.debug(f'Removed repeater {rid_to_int(repeater.repeater_id)} '
                                       f'from route-cache of stream on repeater '
                                       f'{rid_to_int(other_repeater.repeater_id)} slot {other_slot}')
                
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
                # Translate incoming and existing stream for log annotation.
                if repeater.inbound_map:
                    cur_net = repeater.inbound_map.get((current_stream.slot, current_stream.dst_id),
                                                       (current_stream.slot, current_stream.dst_id))
                    new_net = repeater.inbound_map.get((slot, dst_id), (slot, dst_id))
                else:
                    cur_net = (current_stream.slot, current_stream.dst_id)
                    new_net = (slot, dst_id)
                new_ts_tg = fmt_ts_tg(new_net[0], new_net[1], slot, dst_id)

                if current_stream.rf_src == rf_src:
                    if current_stream.dst_id == dst_id:
                        LOGGER.info(f'Same user continuing conversation on repeater {rid_to_int(repeater.repeater_id)} '
                                   f'{new_ts_tg} src={bytes_to_int(rf_src)} during hang time')
                    else:
                        old_ts_tg = fmt_ts_tg(cur_net[0], cur_net[1], current_stream.slot, current_stream.dst_id)
                        LOGGER.info(f'Same user switching talkgroup on repeater {rid_to_int(repeater.repeater_id)} '
                                   f'during hang time: src={bytes_to_int(rf_src)} '
                                   f'old {old_ts_tg} → new {new_ts_tg}')
                        fast_tg_switch = True  # Mark as fast talkgroup switch
                    # Allow by falling through to create new stream
                # Different user - check if same talkgroup
                elif current_stream.dst_id == dst_id:
                    LOGGER.info(f'Different user joining conversation on repeater {rid_to_int(repeater.repeater_id)} '
                               f'{new_ts_tg} during hang time: '
                               f'old_src={bytes_to_int(current_stream.rf_src)} new_src={bytes_to_int(rf_src)}')
                    # Allow by falling through to create new stream
                else:
                    # Different user AND different talkgroup = hijacking attempt
                    old_ts_tg = fmt_ts_tg(cur_net[0], cur_net[1], current_stream.slot, current_stream.dst_id)
                    LOGGER.warning(f'Hang time hijacking blocked on repeater {rid_to_int(repeater.repeater_id)}: '
                                  f'slot reserved for {old_ts_tg}, '
                                  f'denied src={bytes_to_int(rf_src)} attempting {new_ts_tg}')
                    return False
            else:
                # Active stream - different stream_id means contention
                if repeater.inbound_map:
                    cur_net = repeater.inbound_map.get((current_stream.slot, current_stream.dst_id),
                                                       (current_stream.slot, current_stream.dst_id))
                    new_net = repeater.inbound_map.get((slot, dst_id), (slot, dst_id))
                else:
                    cur_net = (current_stream.slot, current_stream.dst_id)
                    new_net = (slot, dst_id)
                cur_ts_tg = fmt_ts_tg(cur_net[0], cur_net[1], current_stream.slot, current_stream.dst_id)
                new_ts_tg = fmt_ts_tg(new_net[0], new_net[1], slot, dst_id)
                LOGGER.warning(f'Stream contention on repeater {rid_to_int(repeater.repeater_id)}: '
                              f'existing {cur_ts_tg} src={bytes_to_int(current_stream.rf_src)} '
                              f'vs new {new_ts_tg} src={bytes_to_int(rf_src)}')

                # Deny the new stream - first come, first served
                return False
        
        # Check if this repeater is allowed to send traffic on this TS/TGID (inbound routing)
        if not self._check_inbound_routing(repeater.repeater_id, slot, dst_id):
            # Track denied streams to avoid logging every packet
            denial_key = (repeater.repeater_id, slot, stream_id)
            current_time = time()
            
            # Only log if this is the first packet of this denied stream
            if denial_key not in self._denied_streams:
                # Special case: repeater used the net-side address for a TG
                # it declared a local alias for. Call it out explicitly so
                # the operator sees it's a mis-keyed address, not an ACL miss.
                if (repeater.inbound_map
                        and (slot, dst_id) in repeater.outbound_map
                        and (slot, dst_id) not in repeater.inbound_map):
                    rf_slot_d, rf_dst_d = repeater.outbound_map[(slot, dst_id)]
                    LOGGER.warning(
                        f'Inbound rejected: repeater={rid_to_int(repeater.repeater_id)} '
                        f'keyed net-side TS{slot}/TG{int.from_bytes(dst_id, "big")} '
                        f'for a translated TG — local side is '
                        f'TS{rf_slot_d}/TG{int.from_bytes(rf_dst_d, "big")}'
                    )
                else:
                    # ACL check ran against net-side vocabulary — show that
                    # in the denial, annotated with the rf-side values when
                    # translated so operators can see both what the radio
                    # keyed and what the server evaluated.
                    if repeater.inbound_map:
                        net_slot_d, net_dst_d = repeater.inbound_map.get((slot, dst_id), (slot, dst_id))
                    else:
                        net_slot_d, net_dst_d = slot, dst_id
                    allowed_tgids = repeater.slot1_talkgroups if net_slot_d == 1 else repeater.slot2_talkgroups
                    allowed_display = sorted(int.from_bytes(tg, 'big') for tg in allowed_tgids) if allowed_tgids else []
                    ts_tg = fmt_ts_tg(net_slot_d, net_dst_d, slot, dst_id)
                    LOGGER.warning(f'Inbound routing denied: repeater={rid_to_int(repeater.repeater_id)} '
                                  f'{ts_tg} not in allowed list {allowed_display}')

                # Add to denied cache
                self._denied_streams[denial_key] = current_time
            
            return False
        
        # Translate source-local → network once for target calculation.
        # Source-side StreamState continues to store LOCAL values so hang-time
        # and contention comparisons stay in the source's vocabulary.
        if repeater.inbound_map:
            net_slot, net_dst_id = repeater.inbound_map.get((slot, dst_id), (slot, dst_id))
        else:
            net_slot, net_dst_id = slot, dst_id

        # Calculate forwarding targets (once per stream, not per packet!)
        # Targets evaluated against NETWORK addressing so every repeater's
        # outbound_map lookup speaks the same vocabulary.
        target_repeaters = self._calculate_stream_targets(
            repeater.repeater_id, net_slot, net_dst_id, stream_id, rf_src
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
        ts_tg = fmt_ts_tg(net_slot, net_dst_id, slot, dst_id)
        fast_tag = ' [FAST TG SWITCH]' if fast_tg_switch else ''
        LOGGER.info(
            f'Group RX stream started on repeater {rid_to_int(repeater.repeater_id)} {ts_tg} '
            f'src={bytes_to_int(rf_src)} targets={len(target_repeaters)} '
            f'stream_id={stream_id.hex()}{fast_tag}'
        )
        
        # Emit stream_start event
        self._emit_stream_start(
            'repeater', 
            int.from_bytes(repeater.repeater_id, 'big'),
            slot,
            rf_src,
            dst_id, 
            stream_id,
            new_stream.call_type,
            False  # RX stream, not assumed
        )
        
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

    def _handle_unit_stream_start(self, repeater: RepeaterState, rf_src: bytes,
                                   dst_id: bytes, slot: int, stream_id: bytes) -> bool:
        """
        Handle the start of a unit (private) call on a repeater slot.

        Unit calls are subscriber-to-subscriber: dst_id holds the target radio
        ID rather than a TGID. Routing uses the user cache; if the target isn't
        known we broadcast to every unit-enabled repeater and let Phase-3
        pruning collapse to one-to-one on the first reply (not implemented
        yet — today broadcasts just persist for the call).

        Slot model is ships-in-the-night: we always forward on the source's
        originating slot regardless of where the target was last heard.

        Hang-time / contention are keyed on the (rf_src, dst_id) subscriber
        pair rather than the talkgroup. Either direction of the same pair
        (A→B or B→A) counts as the same conversation; the same source making
        a new call to a different target is also allowed (like a fast TG
        switch). Anything else during hang time is a hijack and denied.

        Returns True if the stream was accepted and routing cached, False to
        reject the call.
        """
        rid_int = rid_to_int(repeater.repeater_id)
        src_int = bytes_to_int(rf_src)
        dst_int = bytes_to_int(dst_id)

        # Gate at the source: repeater must be enabled for unit calls.
        if not repeater.unit_calls_enabled:
            LOGGER.info(
                f'UNIT CALL rejected on repeater {rid_int} TS{slot}: '
                f'src={src_int} → dst={dst_int} stream_id={stream_id.hex()} '
                f'[repeater has unit_calls_enabled=False]'
            )
            return False

        current_stream = repeater.get_slot_stream(slot)
        current_time = time()

        if current_stream:
            # Same stream continuing
            if current_stream.stream_id == stream_id:
                return True

            if current_stream.ended:
                # Hang time — subscriber-pair semantics for unit calls, TG
                # semantics for a prior group call. When the prior stream was a
                # group call and this is a unit call, the slot was reserved for
                # TG conversation: we treat anything other than the same rf_src
                # as a hijack.
                if current_stream.is_unit_call:
                    same_pair = (
                        (current_stream.rf_src == rf_src and current_stream.dst_id == dst_id)
                        or (current_stream.rf_src == dst_id and current_stream.dst_id == rf_src)
                    )
                    same_src = (current_stream.rf_src == rf_src)
                    if not (same_pair or same_src):
                        LOGGER.warning(
                            f'UNIT CALL hang-time hijack blocked on repeater {rid_int} TS{slot}: '
                            f'slot reserved for {bytes_to_int(current_stream.rf_src)}↔'
                            f'{bytes_to_int(current_stream.dst_id)}, '
                            f'denied src={src_int} → dst={dst_int}'
                        )
                        return False
                else:
                    # Prior stream was a group call; only same source can break through
                    if current_stream.rf_src != rf_src:
                        LOGGER.warning(
                            f'UNIT CALL hang-time hijack blocked on repeater {rid_int} TS{slot}: '
                            f'slot in group-call hang time, denied src={src_int} → dst={dst_int}'
                        )
                        return False
                # fall through to create new stream
            else:
                # Active stream on this slot — contention, first come wins.
                LOGGER.warning(
                    f'UNIT CALL contention on repeater {rid_int} TS{slot}: '
                    f'existing stream_id={current_stream.stream_id.hex()} '
                    f'vs new src={src_int} → dst={dst_int} stream_id={stream_id.hex()}'
                )
                return False

        # Calculate forwarding targets.
        target_repeaters, is_broadcast = self._calculate_unit_call_targets(
            repeater.repeater_id, slot, rf_src, dst_id, stream_id
        )

        new_stream = StreamState(
            repeater_id=repeater.repeater_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            start_time=current_time,
            last_seen=current_time,
            stream_id=stream_id,
            packet_count=1,
            call_type="private",
            target_repeaters=target_repeaters,
            routing_cached=True,
            is_unit_call=True,
            is_broadcast_unit_call=is_broadcast,
        )
        repeater.set_slot_stream(slot, new_stream)

        # Start-of-stream line mirrors the group-call format but with TS/RID in
        # place of TS/TGID and a mode annotation (one-to-one / broadcast /
        # no-targets), plus a cross-slot note when the target was last heard
        # on a different slot than we're forwarding on.
        if is_broadcast:
            mode_tag = '[broadcast]'
        elif target_repeaters:
            target_id = next(iter(target_repeaters))
            target_int = int.from_bytes(target_id, 'big') if isinstance(target_id, bytes) else target_id
            cross_slot = ''
            if self._user_cache:
                entry = self._user_cache.lookup(dst_int)
                if entry and entry.slot != slot:
                    cross_slot = ', cross-slot'
            mode_tag = f'[one-to-one via {target_int}{cross_slot}]'
        else:
            mode_tag = '[no eligible targets]'

        LOGGER.info(
            f'Unit RX stream started on repeater {rid_int} TS/RID: {slot}/{dst_int} '
            f'src={src_int} targets={len(target_repeaters)} '
            f'stream_id={stream_id.hex()} {mode_tag}'
        )

        self._emit_stream_start(
            'repeater',
            rid_int,
            slot,
            rf_src,
            dst_id,
            stream_id,
            'private',
            False,
        )

        # Populate user cache for the source radio. Same call shape as the
        # group-call path; `talkgroup` field holds dst_id here (target radio
        # ID for unit calls), which the dashboard can display as a "called"
        # indicator if it chooses.
        if self._user_cache:
            self._user_cache.update(
                radio_id=src_int,
                repeater_id=rid_int,
                callsign='',
                slot=slot,
                talkgroup=dst_int,
            )

        return True

    def _calculate_unit_call_targets(self, source_repeater_id: Optional[bytes], slot: int,
                                      rf_src: bytes, dst_id: bytes, stream_id: bytes,
                                      source_outbound_name: Optional[str] = None) -> Tuple[set, bool]:
        """
        Build the target set for a unit call.

        Cache hit and cached endpoint eligible → one-to-one target set
        containing a single element: a `repeater_id` (bytes) for local
        repeaters or `('outbound', name)` for outbound connections.
        Cache miss (or cached endpoint ineligible) → broadcast to every
        unit-enabled, connected endpoint (local repeaters + outbound links)
        with the originating slot free, excluding the source endpoint.

        Slot model is ships-in-the-night: we always forward on the source's
        originating slot; the target's last-heard slot is informational only.

        Anti-loop: unit calls that arrived from an outbound connection are
        never re-forwarded to any outbound (including the source) — each
        HBlink4 peer owns fanout within its own network, which yields an
        RPF-style tree when both ends speak the same protocol.

        Args:
            source_repeater_id: Local repeater that sourced the call (bytes),
                or None when the call arrived via an outbound link.
            source_outbound_name: Outbound connection name when the call
                arrived via that link, or None for local-repeater sources.
                Exactly one of source_repeater_id / source_outbound_name
                must be set.

        Returns:
            (target_set, is_broadcast)
        """
        dst_int = bytes_to_int(dst_id)
        from_outbound = source_outbound_name is not None

        # Cache hit path
        if self._user_cache:
            source = self._user_cache.get_source_for_user(dst_int)
            if source is not None:
                kind, ident = source
                if kind == 'local':
                    cached_repeater_id = ident.to_bytes(4, 'big')
                    if cached_repeater_id != source_repeater_id:
                        target_repeater = self._repeaters.get(cached_repeater_id)
                        if (target_repeater
                                and target_repeater.connection_state == 'connected'
                                and target_repeater.unit_calls_enabled
                                and not self._is_slot_busy(cached_repeater_id, slot, stream_id,
                                                           rf_src, dst_id, is_unit_call=True)):
                            return ({cached_repeater_id}, False)
                elif kind == 'outbound' and not from_outbound:
                    # Local sources may route to a cached outbound target;
                    # outbound sources never forward to another outbound.
                    outbound = self._outbounds.get(ident)
                    if (outbound is not None
                            and outbound.authenticated
                            and outbound.config.unit_calls_enabled
                            and not self._is_outbound_slot_busy(outbound, slot, stream_id,
                                                                rf_src, dst_id, is_unit_call=True)):
                        return ({('outbound', ident)}, False)
                # Cache hit but target ineligible (or cross-outbound forwarding
                # suppressed) — fall through to broadcast.

        # Broadcast path: every unit-enabled local repeater except source,
        # plus every unit-enabled outbound except source when call originated
        # locally. Unit calls sourced from an outbound only broadcast to
        # local repeaters (anti-loop).
        target_set: set = set()
        for target_id, target_repeater in self._repeaters.items():
            if target_id == source_repeater_id:
                continue
            if target_repeater.connection_state != 'connected':
                continue
            if not target_repeater.unit_calls_enabled:
                continue
            if self._is_slot_busy(target_id, slot, stream_id, rf_src, dst_id, is_unit_call=True):
                continue
            target_set.add(target_id)

        if not from_outbound:
            for conn_name, outbound in self._outbounds.items():
                if not outbound.authenticated:
                    continue
                if not outbound.config.unit_calls_enabled:
                    continue
                if self._is_outbound_slot_busy(outbound, slot, stream_id,
                                               rf_src, dst_id, is_unit_call=True):
                    continue
                target_set.add(('outbound', conn_name))

        return (target_set, True)

    def _is_outbound_slot_busy(self, outbound: 'OutboundState', slot: int, stream_id: bytes,
                                rf_src: bytes, dst_id: bytes,
                                is_unit_call: bool = False) -> bool:
        """
        Parallel of `_is_slot_busy` for outbound connections.

        Outbound connections track their own per-slot stream state and do
        not pass through the same hang-time helpers; this applies the unit-
        call subscriber-pair hang-time semantics and the group-call TG
        hang-time semantics the same way `_is_slot_busy` does for local
        repeaters.
        """
        current_stream = outbound.get_slot_stream(slot)
        if not current_stream:
            return False
        if current_stream.stream_id == stream_id:
            return False

        current_time = time()
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
        if current_stream.end_time:
            if (current_time - current_stream.end_time) > hang_time:
                return False
            if is_unit_call and current_stream.is_unit_call:
                same_pair = (
                    (current_stream.rf_src == rf_src and current_stream.dst_id == dst_id)
                    or (current_stream.rf_src == dst_id and current_stream.dst_id == rf_src)
                )
                same_src = (current_stream.rf_src == rf_src)
                if same_pair or same_src:
                    return False
            elif not is_unit_call and not current_stream.is_unit_call:
                if current_stream.rf_src == rf_src:
                    return False
                if current_stream.dst_id == dst_id:
                    return False
        return True

    def _handle_stream_packet(self, repeater: RepeaterState, rf_src: bytes, dst_id: bytes,
                              slot: int, stream_id: bytes, call_type_bit: int = 1,
                              frame_type: int = 0, dtype_vseq: int = 0,
                              payload: bytes = b'') -> bool:
        """
        Handle a packet for an ongoing stream.
        Returns True if the packet is valid for the current stream, False otherwise.
        """
        current_stream = repeater.get_slot_stream(slot)

        if not current_stream:
            # No active stream - this is a new stream
            return self._handle_stream_start(repeater, rf_src, dst_id, slot, stream_id,
                                             call_type_bit, frame_type, dtype_vseq, payload)

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
                # Log the fast terminator detection first. Data streams are
                # expected to be single-burst so quiet their fast-terminator
                # log noise down to DEBUG.
                log_fn = LOGGER.debug if current_stream.call_type == 'data' else LOGGER.info
                log_fn(f'Fast terminator: stream on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot} '
                           f'ended via inactivity ({time_since_last_packet*1000:.0f}ms since last packet): '
                           f'src={int.from_bytes(current_stream.rf_src, "big")}, '
                           f'dst={int.from_bytes(current_stream.dst_id, "big")}, '
                           f'duration={(current_time - current_stream.start_time):.2f}s, packets={current_stream.packet_count}')

                # Now use unified ending logic
                self._end_stream(current_stream, repeater.repeater_id, slot, current_time, 'fast_terminator')

                # Don't clear the stream - let _handle_stream_start check hang time
                # It will create the new stream and replace this one if allowed
                return self._handle_stream_start(repeater, rf_src, dst_id, slot, stream_id,
                                                 call_type_bit, frame_type, dtype_vseq, payload)
            elif not current_stream.ended:
                # Real contention - stream still active (within 200ms).
                # Data streams routinely arrive as back-to-back bursts with
                # fresh stream_ids; suppress contention warning for those and
                # silently accept (logged at stream-start dedupe window).
                if current_stream.call_type == 'data':
                    return False
                LOGGER.warning(f'Stream contention on repeater {int.from_bytes(repeater.repeater_id, "big")} slot {slot}: '
                              f'existing stream (src={int.from_bytes(current_stream.rf_src, "big")}, '
                              f'dst={int.from_bytes(current_stream.dst_id, "big")}, '
                              f'active {time_since_last_packet*1000:.0f}ms ago) '
                              f'vs new stream (src={int.from_bytes(rf_src, "big")}, '
                              f'dst={int.from_bytes(dst_id, "big")})')
                return False
            else:
                # Stream already ended (in hang time) - let _handle_stream_start check hang time rules
                return self._handle_stream_start(repeater, rf_src, dst_id, slot, stream_id,
                                                 call_type_bit, frame_type, dtype_vseq, payload)
        
        # Update stream state
        current_stream.last_seen = time()
        current_stream.packet_count += 1
        
        return True
        
    # ========== INBOUND REPEATER MANAGEMENT ==========
        
    def _remove_repeater(self, repeater_id: bytes, reason: str) -> None:
        """
        Remove a repeater and clean up all its state.
        This ensures we don't have any memory leaks from lingering references.
        """
        if repeater_id in self._repeaters:
            repeater = self._repeaters[repeater_id]
            
            # Log current state before removal
            LOGGER.debug(f'Removing repeater {rid_to_int(repeater_id)}: reason={reason}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
            # Emit event before removing so dashboard can update
            self._events.emit('repeater_disconnected', {
                'repeater_id': rid_to_int(repeater_id),
                'callsign': repeater.callsign.decode().strip() if repeater.callsign else 'Unknown',
                'reason': reason
            })
            
            # Remove from active repeaters
            del self._repeaters[repeater_id]
            
            # No cache cleanup needed - using direct conversions to prevent memory leaks
            

    def _handle_repeater_login(self, repeater_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater login request"""
        # Handle both IPv4 (ip, port) and IPv6 (ip, port, flowinfo, scopeid) address formats
        ip = addr[0]
        port = addr[1]
        
        LOGGER.debug(f'Processing login for repeater ID {rid_to_int(repeater_id)} from {ip}:{port}')
        
        # ID Conflict Protection: Check if this ID is reserved for an outbound connection
        # Outbound connections (admin-configured) have priority over inbound repeaters (untrusted)
        repeater_id_int = rid_to_int(repeater_id)
        if repeater_id_int in self._outbound_ids:
            LOGGER.warning(f'⛔ Rejecting inbound repeater {repeater_id_int} from {ip}:{port} '
                         f'- ID reserved for outbound connection')
            self._send_nak(repeater_id, addr, reason="ID reserved for outbound connection")
            return
        
        repeater = self._repeaters.get(repeater_id)
        if repeater:
            if not self._addr_matches_repeater(repeater, addr):
                LOGGER.warning(f'Repeater {rid_to_int(repeater_id)} attempting to connect from {ip}:{port} but already connected from {repeater.ip}:{repeater.port}')
                # Remove the old registration first
                old_addr = repeater.sockaddr
                self._remove_repeater(repeater_id, "reconnect_different_port")
                # Then send NAK to the old address to ensure cleanup
                self._send_nak(repeater_id, old_addr, reason="Repeater reconnecting from new address")
                # Continue with new connection below
            else:
                # Same repeater reconnecting from same IP:port
                old_state = repeater.connection_state
                LOGGER.info(f'Repeater {rid_to_int(repeater_id)} reconnecting while in state {old_state}')
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
                    LOGGER.info(f'Repeater {rid_to_int(repeater_id)} login retry from {ip}:{port}, resending same salt: {repeater.salt}')
                    return
                
        # Create or update repeater state (fresh login)
        repeater = RepeaterState(repeater_id=repeater_id, ip=ip, port=port)
        repeater.connection_state = 'login'
        self._repeaters[repeater_id] = repeater
        
        # Send login ACK with salt
        salt_bytes = repeater.salt.to_bytes(4, 'big')
        self._send_packet(b''.join([RPTACK, salt_bytes]), addr)
        LOGGER.info(f'Repeater {rid_to_int(repeater_id)} login request from {ip}:{port}, sent salt: {repeater.salt}')

    def _handle_auth_response(self, repeater_id: bytes, auth_hash: bytes, addr: PeerAddress) -> None:
        """Handle authentication response from repeater"""
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater or repeater.connection_state != 'login':
            LOGGER.warning(f'Auth response from repeater {rid_to_int(repeater_id)} in wrong state')
            self._send_nak(repeater_id, addr)
            return
            
        try:
            # Get config for this repeater including its passphrase
            repeater_config = self._matcher.get_repeater_config(
                rid_to_int(repeater_id),
                repeater.get_callsign_str()
            )
            
            # If no matching configuration found, reject the connection
            if repeater_config is None:
                LOGGER.warning(f'Repeater {rid_to_int(repeater_id)} does not match any configured patterns and no default is set')
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
                LOGGER.info(f'Repeater {rid_to_int(repeater_id)} authenticated successfully')
            else:
                LOGGER.warning(f'Repeater {rid_to_int(repeater_id)} failed authentication')
                self._send_nak(repeater_id, addr, reason="Authentication failed")
                self._remove_repeater(repeater_id, "auth_failed")
                
        except Exception as e:
            LOGGER.error(f'Authentication error for repeater {rid_to_int(repeater_id)}: {str(e)}')
            self._send_nak(repeater_id, addr)
            self._remove_repeater(repeater_id, "auth_error")

    def _handle_config(self, data: bytes, addr: PeerAddress) -> None:
        """Handle configuration from repeater"""
        try:
            repeater_id = data[4:8]
            repeater = self._validate_repeater(repeater_id, addr)
            if not repeater or not repeater.authenticated or repeater.connection_state != 'config':
                LOGGER.warning(f'Config from repeater {rid_to_int(repeater_id)} in wrong state')
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
            
            # Detect connection type from package_id (primary) and software_id (fallback)
            repeater.connection_type = detect_connection_type(
                repeater.software_id, repeater.package_id, self._config
            )
            
            # Log detailed configuration at debug level
            LOGGER.debug(f'Repeater {rid_to_int(repeater_id)} config:'
                      f'\n    Callsign: {repeater.callsign.decode().strip()}'
                      f'\n    RX Freq: {repeater.rx_freq.decode().strip()}'
                      f'\n    TX Freq: {repeater.tx_freq.decode().strip()}'
                      f'\n    Power: {repeater.tx_power.decode().strip()}'
                      f'\n    ColorCode: {repeater.colorcode.decode().strip()}'
                      f'\n    Location: {repeater.location.decode().strip()}'
                      f'\n    Software: {repeater.software_id.decode().strip()}'
                      f'\n    Package: {repeater.package_id.decode().strip()}'
                      f'\n    Type: {repeater.connection_type}')

            repeater.connected = True
            repeater.connection_state = 'connected'
            
            # Load and cache TG sets from config for fast routing checks
            self._load_repeater_tg_config(repeater_id, repeater)
            
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)
            LOGGER.info(f'Repeater {rid_to_int(repeater_id)} ({repeater.get_callsign_str()}) configured successfully')
            LOGGER.debug(f'Repeater state after config: id={rid_to_int(repeater_id)}, state={repeater.connection_state}, addr={repeater.sockaddr}')
            
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
        rid_int = rid_to_int(repeater_id)
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
            'latitude': safe_decode_bytes(repeater.latitude),
            'longitude': safe_decode_bytes(repeater.longitude),
            'height': safe_decode_bytes(repeater.height),
            'tx_power': safe_decode_bytes(repeater.tx_power),
            'description': safe_decode_bytes(repeater.description),
            'url': safe_decode_bytes(repeater.url),
            'software_id': safe_decode_bytes(repeater.software_id),
            'package_id': safe_decode_bytes(repeater.package_id),
            'connection_type': repeater.connection_type,
            'slots': safe_decode_bytes(repeater.slots),
            'matched_pattern': pattern_name,
            'pattern_description': pattern_desc,
            'match_reason': match_reason
        })

    def _parse_rpto_translation_entry(self, net_slot: int, entry_str: str
                                      ) -> Tuple[Set[bytes], List[Tuple[int, bytes, int, bytes, int]]]:
        """
        Parse a single comma-separated RPTO entry.

        Entry syntax: net_tgid_spec[:local_slot[:local_tgid]]
          net_tgid_spec: N | N-M (range) | N* (prefix; not yet supported) | *
          local_slot:    1 | 2 | *  (* = same as net_slot)
          local_tgid:    N | *      (* = same as matched net_tgid)

        Returns:
            (subscription_tgids, translations)
            subscription_tgids: set of 3-byte net tgids to add to this slot's TG
              subscription set (drives ACL on the network-side vocabulary).
            translations: list of (net_slot, net_tgid_bytes, local_slot,
              local_tgid_bytes, specificity). Empty if the entry has no remap
              clause (pure subscription). Specificity: exact=3, range=2, wildcard=0.
        """
        parts = [p.strip() for p in entry_str.split(':')]
        net_spec = parts[0]
        has_remap = len(parts) > 1

        # --- parse net_tgid_spec ---
        matched_tgids: List[int] = []
        specificity = 0

        if not net_spec:
            raise ValueError('empty net_tgid spec')

        if net_spec == '*':
            if has_remap:
                raise ValueError('wildcards are not supported on the net side — '
                                 'use a specific TGID or a range (e.g., 3000-3200)')
            return (set(), [])

        if net_spec.endswith('*'):
            raise ValueError(f'wildcards are not supported on the net side '
                             f'("{net_spec}") — use a specific TGID or a range '
                             f'(e.g., 9000-9999)')

        if '-' in net_spec:
            try:
                a, b = net_spec.split('-', 1)
                start, end = int(a), int(b)
            except ValueError:
                raise ValueError(f'invalid range: "{net_spec}"')
            if end < start:
                raise ValueError(f'invalid range (end<start): "{net_spec}"')
            if end - start + 1 > 10000:
                raise ValueError(f'range too large to expand: "{net_spec}"')
            matched_tgids = list(range(start, end + 1))
            specificity = 2
        else:
            try:
                matched_tgids = [int(net_spec)]
            except ValueError:
                raise ValueError(f'invalid tgid: "{net_spec}"')
            specificity = 3

        subscription_tgids = {t.to_bytes(3, 'big') for t in matched_tgids}

        if not has_remap:
            return (subscription_tgids, [])

        # --- parse remap part ---
        local_slot_spec = parts[1] if len(parts) >= 2 and parts[1] != '' else '*'
        local_tgid_spec = parts[2] if len(parts) >= 3 and parts[2] != '' else '*'

        if local_slot_spec == '*':
            local_slot = net_slot
        else:
            try:
                local_slot = int(local_slot_spec)
            except ValueError:
                raise ValueError(f'invalid local_slot: "{local_slot_spec}"')
            if local_slot not in (1, 2):
                raise ValueError(f'local_slot must be 1 or 2, got {local_slot}')

        if local_tgid_spec == '*':
            local_tgid_int: Optional[int] = None  # preserve matched net tgid
        else:
            try:
                local_tgid_int = int(local_tgid_spec)
            except ValueError:
                raise ValueError(f'invalid local_tgid: "{local_tgid_spec}"')

        translations: List[Tuple[int, bytes, int, bytes, int]] = []
        for nt in matched_tgids:
            lt = nt if local_tgid_int is None else local_tgid_int
            translations.append((
                net_slot,
                nt.to_bytes(3, 'big'),
                local_slot,
                lt.to_bytes(3, 'big'),
                specificity,
            ))
        return (subscription_tgids, translations)

    def _build_translation_maps(self, repeater_id: bytes,
                                translations: List[Tuple[int, bytes, int, bytes, int]]
                                ) -> Tuple[Dict[Tuple[int, bytes], Tuple[int, bytes]],
                                           Dict[Tuple[int, bytes], Tuple[int, bytes]]]:
        """
        Build inbound/outbound translation maps from a list of translation tuples.
        Most-specific rule wins on collision; less-specific conflicts are dropped
        with a warning. inbound and outbound are inverses.
        """
        # Sort by specificity DESC so the first seen key wins (most specific).
        entries = sorted(translations, key=lambda t: -t[4])

        inbound: Dict[Tuple[int, bytes], Tuple[int, bytes]] = {}
        outbound: Dict[Tuple[int, bytes], Tuple[int, bytes]] = {}
        # Remember which (specificity) claimed each key for collision logging.
        inbound_spec: Dict[Tuple[int, bytes], int] = {}
        outbound_spec: Dict[Tuple[int, bytes], int] = {}

        for net_slot, net_tgid, local_slot, local_tgid, spec in entries:
            net_key = (net_slot, net_tgid)
            local_key = (local_slot, local_tgid)

            if local_key in inbound:
                LOGGER.warning(
                    f'⚠️  Translation collision on repeater {rid_to_int(repeater_id)}: '
                    f'local {get_slot_name(local_key[0])}/TG{int.from_bytes(local_key[1], "big")} '
                    f'already mapped (specificity {inbound_spec[local_key]}); '
                    f'dropping less-specific rule → net {get_slot_name(net_slot)}/'
                    f'TG{int.from_bytes(net_tgid, "big")} (specificity {spec})'
                )
                continue
            if net_key in outbound:
                LOGGER.warning(
                    f'⚠️  Translation collision on repeater {rid_to_int(repeater_id)}: '
                    f'net {get_slot_name(net_key[0])}/TG{int.from_bytes(net_key[1], "big")} '
                    f'already mapped (specificity {outbound_spec[net_key]}); '
                    f'dropping less-specific rule → local {get_slot_name(local_slot)}/'
                    f'TG{int.from_bytes(local_tgid, "big")} (specificity {spec})'
                )
                continue

            inbound[local_key] = net_key
            outbound[net_key] = local_key
            inbound_spec[local_key] = spec
            outbound_spec[net_key] = spec

        return (inbound, outbound)

    def _handle_options(self, repeater_id: bytes, data: bytes, addr: PeerAddress) -> None:
        """
        Handle RPTO message - parse TG options and update repeater's allowed TGs.
        Only TGs that are in the original config are accepted (config has final say).

        Format (backward compatible): TS1=tg1,tg2;TS2=tg3,tg4

        Translation syntax (trusted repeaters only): each comma-separated entry
        may be `net_tgid[:local_slot[:local_tgid]]`. See
        _parse_rpto_translation_entry for the full grammar.

        Outbound rf_src override (trusted repeaters only):
          SRC=9990001
        Every group-voice packet forwarded out of this repeater will have its
        rf_src rewritten to this ID. One-way, group only.
        """
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater:
            return
        
        try:
            # Parse options string
            options_str = data.decode('utf-8', errors='ignore').strip('\x00').strip()
            LOGGER.info(f'📋 OPTIONS from {rid_to_int(repeater_id)} ({repeater.callsign.decode().strip()}): {options_str}')
            
            # Get original config TGs (these are the master allow list)
            repeater_config = self._matcher.get_repeater_config(
                rid_to_int(repeater_id),
                repeater.callsign.decode().strip() if repeater.callsign else None
            )
            
            # Convert config to bytes sets, handling None (allow all) properly
            # None = allow all TGs, [] = deny all, [1,2,3] = specific TGs
            config_ts1 = {tg.to_bytes(3, 'big') for tg in repeater_config.slot1_talkgroups} if repeater_config.slot1_talkgroups is not None else None
            config_ts2 = {tg.to_bytes(3, 'big') for tg in repeater_config.slot2_talkgroups} if repeater_config.slot2_talkgroups is not None else None
            
            # Parse RPTO: TS1=.../TS2=... can hold translation syntax per entry.
            # SRC= declares a single rf_src override applied to every group-voice
            # packet forwarded out of this repeater (one-way).
            # UNIT=true|false declares whether this repeater participates in
            # unit (private) call routing; absent = keep pattern default.
            requested_ts1: Set[bytes] = set()
            requested_ts2: Set[bytes] = set()
            # An empty TS1= / TS2= must mean deny-all on that slot; without these
            # flags it would be indistinguishable from "slot not mentioned" and
            # would fall back to the configured defaults.
            seen_ts1 = False
            seen_ts2 = False
            translations: List[Tuple[int, bytes, int, bytes, int]] = []
            tx_src_override: Optional[bytes] = None
            unit_calls_override: Optional[bool] = None
            saw_translation_syntax = False

            for part in options_str.split(';'):
                part = part.strip()
                if not part or '=' not in part:
                    continue
                key, value = part.split('=', 1)
                key = key.strip().upper()
                value = value.strip()

                if key in ('TS1', 'TS2'):
                    net_slot = 1 if key == 'TS1' else 2
                    target_set = requested_ts1 if net_slot == 1 else requested_ts2
                    if value == '*':
                        # Wildcard: leave as "not specified" so config fallback applies.
                        continue
                    if net_slot == 1:
                        seen_ts1 = True
                    else:
                        seen_ts2 = True
                    if not value:
                        continue
                    for entry in value.split(','):
                        entry = entry.strip()
                        if not entry:
                            continue
                        try:
                            subs, xlates = self._parse_rpto_translation_entry(net_slot, entry)
                        except ValueError as e:
                            LOGGER.warning(
                                f'⚠️  RPTO parse error on {key}="{entry}" from repeater '
                                f'{rid_to_int(repeater_id)}: {e}'
                            )
                            continue
                        target_set.update(subs)
                        if xlates:
                            saw_translation_syntax = True
                            translations.extend(xlates)
                elif key == 'SRC':
                    saw_translation_syntax = True
                    if not value:
                        continue
                    try:
                        src_id_int = int(value)
                        if not 0 < src_id_int < 0x1000000:
                            raise ValueError('out of 24-bit range')
                        tx_src_override = src_id_int.to_bytes(3, 'big')
                    except ValueError as e:
                        LOGGER.warning(
                            f'⚠️  RPTO SRC parse error from repeater '
                            f'{rid_to_int(repeater_id)}: "{value}" ({e})'
                        )
                elif key == 'UNIT':
                    v = value.lower()
                    if v in ('true', '1', 'yes', 'on'):
                        unit_calls_override = True
                    elif v in ('false', '0', 'no', 'off'):
                        unit_calls_override = False
                    else:
                        LOGGER.warning(
                            f'⚠️  RPTO UNIT parse error from repeater '
                            f'{rid_to_int(repeater_id)}: "{value}" (expected true/false)'
                        )
                        continue
                    # UNIT= override is honored only for trusted repeaters.
                    # Untrusted repeaters stay on the pattern default.
                    if not repeater_config.trust:
                        LOGGER.warning(
                            f'⚠️  Repeater {rid_to_int(repeater_id)} sent UNIT={value} '
                            f'but is not trusted — override ignored, pattern default used'
                        )
                        unit_calls_override = None
            
            # Check if this repeater is trusted
            if repeater_config.trust:
                # Trusted repeater: requested TGs are taken as-is; slots the
                # repeater didn't mention fall back to the configured defaults.
                final_ts1 = requested_ts1 if seen_ts1 else config_ts1
                final_ts2 = requested_ts2 if seen_ts2 else config_ts2

                # Log trust usage - show any TGs beyond config (informational, not warning)
                if config_ts1 is not None and requested_ts1:
                    extra_ts1 = requested_ts1 - config_ts1
                    if extra_ts1:
                        extra_ts1_ints = sorted(int.from_bytes(tg, 'big') for tg in extra_ts1)
                        LOGGER.info(f'🔓 Trusted repeater {rid_to_int(repeater_id)} using additional TS1 TGs: {extra_ts1_ints}')
                if config_ts2 is not None and requested_ts2:
                    extra_ts2 = requested_ts2 - config_ts2
                    if extra_ts2:
                        extra_ts2_ints = sorted(int.from_bytes(tg, 'big') for tg in extra_ts2)
                        LOGGER.info(f'🔓 Trusted repeater {rid_to_int(repeater_id)} using additional TS2 TGs: {extra_ts2_ints}')

                rejected_ts1 = set()
                rejected_ts2 = set()
            else:
                # Standard behavior: intersection of requested and config.
                # If a slot was not mentioned in RPTO, fall back to config; if it
                # was mentioned (even as an empty list), honor the request and
                # only intersect with config when config has explicit TGs.
                if not seen_ts1:
                    final_ts1 = config_ts1
                elif config_ts1 is None:
                    final_ts1 = requested_ts1
                else:
                    final_ts1 = requested_ts1 & config_ts1

                if not seen_ts2:
                    final_ts2 = config_ts2
                elif config_ts2 is None:
                    final_ts2 = requested_ts2
                else:
                    final_ts2 = requested_ts2 & config_ts2

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
                rejected_ts1_ints = sorted(int.from_bytes(tg, 'big') for tg in rejected_ts1)
                LOGGER.warning(f'⚠️  TS1 TG(s) {rejected_ts1_ints} requested by repeater {rid_to_int(repeater_id)} not allowed by config')
            if rejected_ts2:
                rejected_ts2_ints = sorted(int.from_bytes(tg, 'big') for tg in rejected_ts2)
                LOGGER.warning(f'⚠️  TS2 TG(s) {rejected_ts2_ints} requested by repeater {rid_to_int(repeater_id)} not allowed by config')
            
            # Replace repeater's TG sets (no need to keep old ones)
            repeater.slot1_talkgroups = final_ts1
            repeater.slot2_talkgroups = final_ts2
            repeater.rpto_received = True  # Mark that RPTO was received

            # UNIT= is a full replacement like TGs and SRC: absent UNIT reverts
            # to the pattern default, present UNIT overrides it for this repeater.
            new_unit_enabled = (
                unit_calls_override if unit_calls_override is not None
                else repeater_config.default_unit_calls
            )
            if new_unit_enabled != repeater.unit_calls_enabled:
                repeater.unit_calls_enabled = new_unit_enabled
                LOGGER.info(
                    f'  → Unit calls {"ENABLED" if new_unit_enabled else "DISABLED"} '
                    f'on repeater {rid_to_int(repeater_id)}'
                    f'{" (RPTO override)" if unit_calls_override is not None else " (pattern default)"}'
                )

            # Build translation maps. Only trusted repeaters may declare remaps
            # or rf_src override; untrusted repeaters get translation syntax
            # silently ignored (their TG subscription is still honored).
            if saw_translation_syntax and not repeater_config.trust:
                LOGGER.warning(
                    f'⚠️  Repeater {rid_to_int(repeater_id)} sent translation syntax '
                    f'but is not trusted — translation rules ignored'
                )
                new_inbound: Dict[Tuple[int, bytes], Tuple[int, bytes]] = {}
                new_outbound: Dict[Tuple[int, bytes], Tuple[int, bytes]] = {}
                new_tx_src_override: Optional[bytes] = None
            elif repeater_config.trust:
                new_inbound, new_outbound = self._build_translation_maps(repeater_id, translations)
                new_tx_src_override = tx_src_override
            else:
                new_inbound = {}
                new_outbound = {}
                new_tx_src_override = None

            # Warn if RPTO arrives mid-stream; apply anyway (asyncio is single
            # threaded and the active stream will flush in seconds).
            active_stream = False
            for s in (1, 2):
                st = repeater.get_slot_stream(s)
                if st and not st.ended:
                    active_stream = True
                    break
            if active_stream and (new_inbound or new_outbound
                                  or repeater.inbound_map or repeater.outbound_map):
                LOGGER.warning(
                    f'⚠️  RPTO received during active stream on repeater '
                    f'{rid_to_int(repeater_id)} — translation rules updated, '
                    f'takes effect on next stream'
                )

            repeater.inbound_map = new_inbound
            repeater.outbound_map = new_outbound
            repeater.tx_src_override = new_tx_src_override

            # Log final TG lists (handle None = allow all)
            LOGGER.info(f'  → TS1 TGs: {self._format_tg_display(final_ts1)}')
            LOGGER.info(f'  → TS2 TGs: {self._format_tg_display(final_ts2)}')
            if new_inbound:
                LOGGER.info(f'  → Translation rules: {len(new_inbound)} active')
                for (lslot, ltgid), (nslot, ntgid) in sorted(new_inbound.items()):
                    LOGGER.info(
                        f'      local {get_slot_name(lslot)}/TG{int.from_bytes(ltgid, "big")} '
                        f'↔ net {get_slot_name(nslot)}/TG{int.from_bytes(ntgid, "big")}'
                    )
            if new_tx_src_override is not None:
                LOGGER.info(
                    f'  → Outbound rf_src override: {int.from_bytes(new_tx_src_override, "big")} '
                    f'(group voice only)'
                )
            
            # Emit event to update dashboard in real-time. Translations are
            # emitted as [rf_slot, rf_tgid, net_slot, net_tgid] tuples so the
            # dashboard can show RF-side TGIDs on each card with a back-ref
            # tooltip to the network side.
            translations_list = [
                [lslot, int.from_bytes(ltgid, 'big'),
                 nslot, int.from_bytes(ntgid, 'big')]
                for (lslot, ltgid), (nslot, ntgid) in sorted(new_inbound.items())
            ]
            self._events.emit('repeater_options_updated', {
                'repeater_id': rid_to_int(repeater_id),
                'slot1_talkgroups': self._format_tg_json(final_ts1),
                'slot2_talkgroups': self._format_tg_json(final_ts2),
                'rpto_received': True,
                'translations': translations_list
            })
            
            # Send ACK
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)
            
        except Exception as e:
            LOGGER.error(f'Error processing RPTO from {rid_to_int(repeater_id)}: {e}')
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
            LOGGER.debug(f'📻 Talker Alias from {rid_to_int(repeater_id)} ({repeater.get_callsign_str()})')
            
            # TODO: Future enhancement - parse talker alias blocks and emit to dashboard
            # Talker alias format: https://github.com/g4klx/MMDVMHost/wiki/Talker-Alias
            
            # Send ACK to confirm receipt
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)
            
        except Exception as e:
            LOGGER.error(f'Error processing DMRA from {rid_to_int(repeater_id)}: {e}')
            # Still send ACK to avoid retries
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)

    def _handle_ping(self, repeater_id: bytes, addr: PeerAddress) -> None:
        """Handle ping (RPTPING/RPTP) from the repeater as a keepalive."""
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater or repeater.connection_state != 'connected':
            LOGGER.warning(f'Ping from repeater {rid_to_int(repeater_id)} in wrong state (state="{repeater.connection_state}" if repeater else "None")')
            self._send_nak(repeater_id, addr, reason="Wrong connection state")
            return
            
        # Update ping time and reset missed pings
        repeater.last_ping = time()
        had_missed_pings = repeater.missed_pings > 0
        if had_missed_pings:
            LOGGER.info(f'Ping counter reset for repeater {rid_to_int(repeater_id)} after {repeater.missed_pings} missed pings')
        repeater.missed_pings = 0
        repeater.ping_count += 1
        
        # Emit event to update dashboard if we had missed pings (to clear warning)
        if had_missed_pings:
            self._events.emit('repeater_connected', self._prepare_repeater_event_data(repeater_id, repeater))
        
        # Send MSTPONG in response to RPTPING/RPTP from repeater
        LOGGER.debug(f'Sending MSTPONG to repeater {rid_to_int(repeater_id)}')
        self._send_packet(b''.join([MSTPONG, repeater_id]), addr)

    def _handle_disconnect(self, repeater_id: bytes, addr: PeerAddress) -> None:
        """Handle repeater disconnect"""
        repeater = self._validate_repeater(repeater_id, addr)
        if repeater:
            LOGGER.info(f'Repeater {rid_to_int(repeater_id)} ({repeater.get_callsign_str()}) disconnected')
            self._remove_repeater(repeater_id, "disconnect")
            
    def _handle_status(self, repeater_id: bytes, data: bytes, addr: PeerAddress) -> None:
        """Handle repeater status report (including RSSI)"""
        repeater = self._validate_repeater(repeater_id, addr)
        if repeater:
            LOGGER.debug(f'Status report from repeater {rid_to_int(repeater_id)}: {data[8:].hex()}')
            self._send_packet(b''.join([RPTACK, repeater_id]), addr)

    def _is_dmr_terminator(self, data: bytes, frame_type: int) -> bool:
        """DMR terminator detection - delegated to protocol module"""
        return is_dmr_terminator(data, frame_type)
    
    def _calculate_stream_targets(self, source, slot: int,
                                  dst_id: bytes, stream_id: bytes, rf_src: bytes) -> set:
        """
        Calculate which repeaters, outbound connections, AND OpenBridges should receive this ENTIRE transmission.

        `source` is the origin identifier, used only to avoid echoing back to it:
        a repeater_id (bytes) or an ('openbridge', name) tuple. Equality is
        checked against each candidate; a repeater_id never equals a tuple, so
        the self-skip is correct regardless of source type.
        
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
        target_set = set()
        
        # Calculate local repeater targets.
        # `slot`/`dst_id` are network-side values — each target may remap them
        # to its own local slot/tgid before landing on the air.
        for target_repeater_id, target_repeater in self._repeaters.items():
            # Skip source (a repeater_id never equals an ('openbridge', name) tuple)
            if target_repeater_id == source:
                continue

            # Only forward to connected repeaters
            if target_repeater.connection_state != 'connected':
                continue

            # Check outbound routing (TG allowed on this repeater/slot, network vocab)
            if not self._check_outbound_routing(target_repeater_id, slot, dst_id):
                continue

            # Resolve the target's LOCAL slot/tgid for busy/hang-time checks.
            # A translated repeater's physical slot can differ from the net slot,
            # so we must check the slot the packet will actually occupy on air.
            if target_repeater.outbound_map:
                local = target_repeater.outbound_map.get((slot, dst_id))
                if local is not None:
                    check_slot, check_dst = local
                else:
                    check_slot, check_dst = slot, dst_id
            else:
                check_slot, check_dst = slot, dst_id

            # Check slot availability AT STREAM START (not per-packet!)
            # If busy now, exclude from this transmission entirely
            if self._is_slot_busy(target_repeater_id, check_slot, stream_id, rf_src, check_dst):
                LOGGER.debug(f'Target repeater {int.from_bytes(target_repeater_id, "big")} '
                           f'TS{check_slot} busy at stream start, excluded from this transmission')
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
            if allowed_tgs is not None and (not allowed_tgs or dst_id not in allowed_tgs):
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

        # OpenBridge (OBP) targets. OBP is stream-multiplexed and TGID-transparent:
        # ownership is by canonical TGID only (no slot/contention gating), and
        # Position B means no target remap. The reflection guard is the same
        # self-skip used above — never send back out the source OBP.
        for obp_name, obp in self._openbridges.items():
            if source == ('openbridge', obp_name):
                continue
            if dst_id in obp.config.talkgroup_slots:
                target_set.add(('openbridge', obp_name))

        return target_set
    
    def _forward_stream(self, data: bytes, source, slot: int,
                       rf_src: bytes, dst_id: bytes, stream_id: bytes) -> None:
        """
        Forward DMR stream to target repeaters using cached routing.

        Targets are calculated ONCE at stream start. No per-packet checks needed!

        When translation is in play we may rewrite:
          - bytes  5-7  rf_src (subscriber NAT, if configured on source)
          - bytes  8-10 dst_id (target-local or network, per target's outbound_map)
          - byte   15   slot bit (follows translated slot)
          - bytes 20-52 payload: for LC-bearing frames we re-encode the Link
            Control to match the outbound addressing. VHEAD (frame_type=2,
            dtype_vseq=1) and VTERM (frame_type=2, dtype_vseq=2) frames get
            their 196-bit BPTC LC codeword replaced, preserving the 68-bit
            slot-type/sync window at bits [98:166]. Voice bursts B/C/D/E
            (voice frame, dtype_vseq 1..4) get their 32-bit embedded LC
            fragment at bits [116:148] replaced — AMBE vocoder bits are
            untouched. All other frames (burst A, CSBK, data headers) pass
            through with payload intact.

        Args:
            data: Complete DMRD packet (20-byte HBP header + 33-byte DMR data)
            source: origin identifier — a repeater_id (bytes) or ('openbridge', name)
            slot: Source-local timeslot (1 or 2)
            rf_src: RF source subscriber ID (3 bytes) — source-local
            dst_id: Destination TGID (3 bytes) — source-local
            stream_id: Unique stream identifier (4 bytes)
        """
        # Resolve the source (repeater or OpenBridge). An OBP source has no
        # translation (Position B, identity), and its stream lives in the OBP's
        # stream dict rather than a repeater slot. source_peer_id is the true
        # originating peer, used when framing for an OBP target.
        if isinstance(source, tuple) and source[0] == 'openbridge':
            source_obp = self._openbridges.get(source[1])
            if not source_obp:
                return
            source_stream = source_obp.streams.get(stream_id)
            src_inbound_map = None
            src_tx_override = None
            source_disp_id = source_obp.config.network_id
            source_peer_id = (source_stream.repeater_id if source_stream
                              else source_obp.config.network_id.to_bytes(4, 'big'))
        else:
            source_repeater = self._repeaters.get(source)
            if not source_repeater:
                return
            source_stream = source_repeater.get_slot_stream(slot)
            src_inbound_map = source_repeater.inbound_map
            src_tx_override = source_repeater.tx_src_override
            source_disp_id = int.from_bytes(source, 'big')
            source_peer_id = source  # repeater_id (4 bytes) — true source peer

        if not source_stream or source_stream.stream_id != stream_id:
            # This shouldn't happen, but safety check
            LOGGER.warning(f'Forwarding called but no matching stream found')
            return

        # Translate source-local → network ONCE. All target lookups use network
        # keys (outbound_map is keyed on network values). OBP source = identity.
        if src_inbound_map:
            net_slot, net_dst_id = src_inbound_map.get((slot, dst_id), (slot, dst_id))
        else:
            net_slot, net_dst_id = slot, dst_id

        # Outbound rf_src override: replace EVERY rf_src from this repeater with
        # a single network-side ID. Group voice only — call_type bit is bit 6 of
        # byte 15 (0 = group, 1 = private/unit). Unit calls are rejected upstream
        # today, but gate here too so the override stays scoped to group voice.
        call_type_bit = (data[15] & 0x40) >> 6
        if src_tx_override is not None and call_type_bit == 0:
            net_rf_src = src_tx_override
        else:
            net_rf_src = rf_src

        # Use cached target list (calculated once on stream start!)
        if not source_stream.routing_cached or source_stream.target_repeaters is None:
            # Safety fallback (shouldn't happen)
            LOGGER.warning(f'Stream routing not cached, recalculating')
            source_stream.target_repeaters = self._calculate_stream_targets(
                source, net_slot, net_dst_id, stream_id, net_rf_src
            )
            source_stream.routing_cached = True

        # Check if this is a terminator packet (use original data bits for check)
        _bits = data[15]
        _frame_type = (_bits & 0x30) >> 4
        _dtype_vseq = _bits & 0xF
        is_terminator = self._is_dmr_terminator(data, _frame_type)

        # Does this frame carry an LC we need to rewrite under translation?
        # Only VHEAD/VTERM (full LC) and voice bursts B/C/D/E (EMB_LC) do.
        lc_carrier = classify_lc_carrier(_frame_type, _dtype_vseq)

        # Lazily capture the stream's source-of-truth LC. Prefer decoding
        # the originator's VHEAD so FLCO/FID/service-options survive; fall
        # back to a synthesized group-voice LC for late-entry streams where
        # VHEAD was missed. Private/unit-call LC will slot in here when
        # those calls are enabled — today only group voice is forwarded.
        if source_stream.lc_base is None:
            decoded_lc: Optional[bytes] = None
            if lc_carrier == LC_CARRIER_VHEAD:
                decoded_lc = decode_lc_from_vhead(data[20:53])
            if decoded_lc is not None:
                source_stream.lc_base = decoded_lc
            else:
                source_stream.lc_base = synth_lc_base(dst_id, rf_src)

        # Source-side translation = source's own addressing diverges from
        # what goes on the wire. Drives whether targets need rewrite at all.
        source_translated = (net_slot, net_dst_id) != (slot, dst_id) or net_rf_src != rf_src

        lc_opts = source_stream.lc_base[0:3]
        lc_cache = source_stream.lc_cache

        def get_encoded_lc(out_dst: bytes, out_src: bytes):
            """Return cached (h_lc, t_lc, emb_lc) for this target addressing.

            One BPTC encode per (dst, src) tuple per stream — subsequent
            frames hit the cache. Slot isn't keyed because LC doesn't carry
            slot info.
            """
            key = (out_dst, out_src)
            entry = lc_cache.get(key)
            if entry is None:
                entry = encode_lc_forms(build_lc(lc_opts, out_dst, out_src))
                lc_cache[key] = entry
            return entry

        def build_target_packet(out_slot: int, out_dst: bytes, out_src: bytes,
                                out_repeater_id: Optional[bytes]) -> bytes:
            """Return a DMRD packet rewritten for this target's addressing."""
            # Fast path: no header rewrite needed AND no LC rewrite needed.
            header_unchanged = (
                out_slot == slot
                and out_dst == dst_id
                and out_src == rf_src
                and out_repeater_id is None
            )
            if header_unchanged and lc_carrier == LC_CARRIER_NONE:
                return data
            # If the outbound addressing exactly matches what the originator
            # already put in the LC, its own payload is correct — no splice.
            lc_needs_rewrite = lc_carrier != LC_CARRIER_NONE and (
                out_dst != dst_id or out_src != rf_src
            )
            if header_unchanged and not lc_needs_rewrite:
                return data

            buf = bytearray(data)
            if out_src != rf_src:
                buf[5:8] = out_src
            if out_dst != dst_id:
                buf[8:11] = out_dst
            if out_repeater_id is not None:
                buf[11:15] = out_repeater_id
            # Slot bit: bit7 of byte 15 (0=TS1, 1=TS2).
            current_slot_bit = 2 if (buf[15] & 0x80) else 1
            if out_slot != current_slot_bit:
                if out_slot == 2:
                    buf[15] |= 0x80
                else:
                    buf[15] &= 0x7F

            if lc_needs_rewrite:
                h_lc, t_lc, emb_lc = get_encoded_lc(out_dst, out_src)
                payload = bytes(buf[20:53])
                if lc_carrier == LC_CARRIER_VHEAD:
                    buf[20:53] = splice_full_lc(payload, h_lc)
                elif lc_carrier == LC_CARRIER_VTERM:
                    buf[20:53] = splice_full_lc(payload, t_lc)
                elif lc_carrier == LC_CARRIER_EMB:
                    buf[20:53] = splice_emb_lc(payload, emb_lc[_dtype_vseq])
            return bytes(buf)

        # Simple loop through cached targets - no per-packet checks!
        for target in source_stream.target_repeaters:
            # Check if target is an outbound connection or local repeater
            if isinstance(target, tuple) and target[0] == 'outbound':
                # Target is an outbound connection (we're acting as a repeater to remote server)
                conn_name = target[1]
                outbound = self._outbounds.get(conn_name)
                if not outbound or not outbound.authenticated:
                    continue  # Connection dropped mid-stream

                # Outbound server speaks network-side vocabulary — no local remap.
                our_id_bytes = outbound.config.radio_id.to_bytes(4, 'big')
                packet = build_target_packet(net_slot, net_dst_id, net_rf_src, our_id_bytes)
                outbound.transport.sendto(packet)

                # Track assumed stream state on outbound slot (TDMA constraint)
                # We must track what we're transmitting on each timeslot
                self._update_assumed_stream_outbound(outbound, net_slot, net_rf_src, net_dst_id,
                                                    stream_id, is_terminator,
                                                    source_disp_id,
                                                    is_unit_call=source_stream.is_unit_call)

            elif isinstance(target, tuple) and target[0] == 'openbridge':
                # OBP target: canonical network-side addressing, no target remap
                # (Position B). Build the net-side DMRD, then frame it for OBP —
                # RptrId per preserve_source_peer, wire slot forced to 1, HMAC.
                obp = self._openbridges.get(target[1])
                if not obp:
                    continue
                dmrd53 = build_target_packet(net_slot, net_dst_id, net_rf_src, None)
                obp.transport.sendto(
                    self._obp_build_egress(obp, dmrd53, source_peer_id), obp.sockaddr)

                # Track the assumed TX stream so the dashboard draws an outbound
                # pill and the stream ends cleanly (terminator or reaper backstop).
                self._update_assumed_stream_obp(
                    obp, net_slot, net_rf_src, net_dst_id, stream_id, is_terminator,
                    is_unit_call=source_stream.is_unit_call)

            else:
                # Target is a local repeater (bytes)
                target_repeater_id = target
                target_repeater = self._repeaters.get(target_repeater_id)
                if not target_repeater:
                    continue  # Repeater disconnected mid-stream

                # Per-target translation: network → target-local (if mapped).
                if target_repeater.outbound_map:
                    t_local = target_repeater.outbound_map.get((net_slot, net_dst_id))
                    if t_local is not None:
                        out_slot, out_dst = t_local
                    else:
                        out_slot, out_dst = net_slot, net_dst_id
                else:
                    out_slot, out_dst = net_slot, net_dst_id

                # Fast path: no source translation, no target translation, no
                # LC rewrite needed — ship the original buffer as-is.
                if (lc_carrier == LC_CARRIER_NONE
                        and not source_translated
                        and (out_slot, out_dst) == (slot, dst_id)
                        and net_rf_src == rf_src):
                    self._send_packet(data, target_repeater.sockaddr)
                else:
                    packet = build_target_packet(out_slot, out_dst, net_rf_src, None)
                    self._send_packet(packet, target_repeater.sockaddr)

                # Track assumed stream state on target repeater using target-local values
                self._update_assumed_stream(target_repeater, out_slot, net_rf_src, out_dst,
                                           stream_id, is_terminator,
                                           source_disp_id,
                                           net_slot=net_slot, net_dst_id=net_dst_id,
                                           is_unit_call=source_stream.is_unit_call)
    
    # ================================
    # DMR Packet Processing
    # ================================
    
    def _parse_dmr_packet(self, data: bytes) -> Optional[Dict[str, Any]]:
        """Parse DMR packet - delegated to protocol module"""
        return parse_dmr_packet(data)
    
# _safe_decode_bytes moved to utils.py

    def _handle_dmr_data(self, data: bytes, addr: PeerAddress) -> None:
        """Handle DMR data"""
        # Parse packet using unified parser
        packet = self._parse_dmr_packet(data)
        if not packet:
            LOGGER.warning(f'Invalid DMR data packet from {addr[0]}:{addr[1]} - length {len(data)} < 55')
            return
            
        repeater_id = packet['repeater_id']
        repeater = self._validate_repeater(repeater_id, addr)
        if not repeater or repeater.connection_state != 'connected':
            LOGGER.warning(f'DMR data from repeater {packet["repeater_id_int"]} in wrong state')
            return
            
        # Extract fields from parsed packet
        _seq = packet['seq']
        _rf_src = packet['rf_src']
        _dst_id = packet['dst_id']
        _slot = packet['slot']
        _call_type = packet['call_type']
        _frame_type = packet['frame_type']
        _stream_id = packet['stream_id']
        _dtype_vseq = data[15] & 0x0F
        _payload = data[20:53] if len(data) >= 53 else b''

        # Check if this is a stream terminator (immediate end detection)
        # Note: _is_dmr_terminator() checks packet header flags for immediate detection
        _is_terminator = self._is_dmr_terminator(data, _frame_type)

        # Handle stream tracking
        stream_valid = self._handle_stream_packet(
            repeater, _rf_src, _dst_id, _slot, _stream_id, _call_type,
            _frame_type, _dtype_vseq, _payload,
        )

        if not stream_valid:
            # Stream contention or not allowed - drop packet silently
            LOGGER.debug(f'Dropped packet from repeater {rid_to_int(repeater_id)} slot {_slot}: '
                        f'src={int.from_bytes(_rf_src, "big")}, dst={int.from_bytes(_dst_id, "big")}, '
                        f'reason=stream contention or talkgroup not allowed')
            return

        # Get the current stream for this slot (after _handle_stream_packet has updated it)
        current_stream = repeater.get_slot_stream(_slot)

        # Data streams are tracked (so fast-terminator/contention logic stays
        # quiet) and emitted to the dashboard, but never forwarded. Drop here
        # before the forwarding path and before stream_update telemetry —
        # data calls don't produce meaningful packet-count telemetry anyway.
        if current_stream and current_stream.call_type == 'data':
            return
        
        # Per-packet logging - only enable for heavy troubleshooting
        #LOGGER.debug(f'DMR data from {packet["repeater_id_int"]} slot {_slot}: '
        #            f'seq={_seq}, src={packet["src_id_int"]}, '
        #            f'dst={packet["dst_id_int"]}, '
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
                'repeater_id': rid_to_int(repeater_id),
                'slot': _slot,
                'src_id': int.from_bytes(current_stream.rf_src, 'big'),
                'dst_id': int.from_bytes(current_stream.dst_id, 'big'),
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
                              source_repeater_id: int,
                              net_slot: int = None, net_dst_id: bytes = None,
                              is_unit_call: bool = False) -> None:
        """
        Update or create assumed stream state on a target repeater.

        Since we're forwarding to this repeater but not receiving feedback,
        we must assume the stream state based on what we're sending.

        Args:
            repeater: Target repeater state
            slot: Timeslot (target-local, i.e. what the target's RF side sees)
            rf_src: Source subscriber ID
            dst_id: Destination TGID (target-local)
            stream_id: Stream identifier
            is_terminator: Whether this packet is a terminator
            source_repeater_id: ID of source repeater (for logging)
            net_slot: Network-side timeslot (for log annotation when translated)
            net_dst_id: Network-side TGID (for log annotation when translated)
            is_unit_call: True if this is a unit (private) call — governs
                hang-time semantics (subscriber pair) if a new stream arrives
                on this slot after the assumed one ends.
        """
        current_stream = repeater.get_slot_stream(slot)
        current_time = time()

        if not current_stream or current_stream.stream_id != stream_id:
            # New assumed stream starting
            call_type = "private" if is_unit_call else "group"
            new_stream = StreamState(
                repeater_id=repeater.repeater_id,
                rf_src=rf_src,
                dst_id=dst_id,
                slot=slot,
                start_time=current_time,
                last_seen=current_time,
                stream_id=stream_id,
                packet_count=1,
                call_type=call_type,
                is_assumed=True,
                is_unit_call=is_unit_call,
            )
            repeater.set_slot_stream(slot, new_stream)

            # Log at DEBUG level - TX streams are noisy
            if is_unit_call:
                ts_addr = f'TS/RID: {slot}/{bytes_to_int(dst_id)}'
                call_type_prefix = 'Unit'
            else:
                ts_addr = fmt_ts_tg(net_slot if net_slot is not None else slot,
                                    net_dst_id if net_dst_id is not None else dst_id,
                                    slot, dst_id)
                call_type_prefix = 'Group'
            LOGGER.debug(
                f'{call_type_prefix} TX stream started on repeater {rid_to_int(repeater.repeater_id)} '
                f'{ts_addr} from repeater {source_repeater_id} src={bytes_to_int(rf_src)}'
            )

            # Emit stream_start event for repeater card display (but marked as assumed)
            # Dashboard will filter these from Recent Events log
            self._emit_stream_start(
                'repeater',
                int.from_bytes(repeater.repeater_id, 'big'),
                slot,
                rf_src,
                dst_id,
                stream_id,
                call_type,
                True  # TX assumed stream
            )
            
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
                                       source_repeater_id: int,
                                       is_unit_call: bool = False) -> None:
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
            call_type = "private" if is_unit_call else "group"

            new_stream = StreamState(
                repeater_id=dummy_id,  # Our ID when acting as repeater
                rf_src=rf_src,
                dst_id=dst_id,
                slot=slot,
                start_time=current_time,
                last_seen=current_time,
                stream_id=stream_id,
                packet_count=1,
                call_type=call_type,
                is_assumed=True,  # Mark as assumed stream (TX, not RX)
                is_unit_call=is_unit_call,
            )
            outbound.set_slot_stream(slot, new_stream)

            # Emit stream_start event for dashboard (using outbound connection name as identifier)
            # Keep structure minimal and JSON-serializable (match repeater-style fields
            # where possible). Do NOT include UserEntry objects (callsign) here.
            self._emit_stream_start(
                'outbound',
                outbound.config.name,
                slot,
                rf_src,
                dst_id,
                stream_id,
                call_type,
                True  # TX assumed stream
            )
            
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
            
            # Emit stream_end event for dashboard
            self._emit_stream_end(
                'outbound',
                outbound.config.name,
                slot,
                current_stream,
                'terminator'
            )

    def _update_assumed_stream_obp(self, obp: 'OpenBridgeState', slot: int, rf_src: bytes,
                                   dst_id: bytes, stream_id: bytes, is_terminator: bool,
                                   is_unit_call: bool = False) -> None:
        """Track and emit dashboard events for a TX (assumed) stream on an OBP trunk.

        OBP is stream-multiplexed (no TDMA slot), so the assumed TX stream is
        keyed by stream_id in the trunk's own stream dict — the same dict the
        RX/ingress path uses and the stale-stream reaper already sweeps (source
        != target for any stream, so RX and TX entries never collide). Marked
        is_assumed=True so the dashboard renders it as an outbound pill. Mirrors
        the OBP ingress emit and deliberately does NOT touch _active_calls,
        matching ingress (OBP streams are not counted toward the active total).
        """
        current_time = time()
        stream = obp.streams.get(stream_id)
        if stream is None:
            call_type = "private" if is_unit_call else "group"
            stream = StreamState(
                repeater_id=obp.config.network_id.to_bytes(4, 'big'),  # our ID (TX)
                rf_src=rf_src,
                dst_id=dst_id,
                slot=slot,
                start_time=current_time,
                last_seen=current_time,
                stream_id=stream_id,
                packet_count=1,
                call_type=call_type,
                is_assumed=True,  # TX, not received from the peer
                is_unit_call=is_unit_call,
            )
            obp.streams[stream_id] = stream
            LOGGER.info(f'[OBP {obp.config.name}] TX stream start '
                        f'src={int.from_bytes(rf_src, "big")} '
                        f'tgid={int.from_bytes(dst_id, "big")} -> TS{slot} '
                        f'stream_id={stream_id.hex()}')
            self._emit_stream_start(
                'openbridge',
                obp.config.name,
                slot,
                rf_src,
                dst_id,
                stream_id,
                call_type,
                True,  # TX assumed stream
            )
        else:
            stream.last_seen = current_time
            stream.packet_count += 1

        if is_terminator and not stream.ended:
            stream.ended = True
            stream.end_time = current_time
            LOGGER.info(f'[OBP {obp.config.name}] TX stream end '
                        f'stream_id={stream_id.hex()} packets={stream.packet_count}')
            self._emit_stream_end('openbridge', obp.config.name, slot, stream, 'terminator')


    # ------------------------------------------------------------------
    # OpenBridge (OBP) — stream-multiplexed, TGID-transparent trunk.
    # Wire frame: 53-byte DMRD + 20-byte HMAC-SHA1(passphrase, DMRD).
    # No login/keepalive; auth is the per-packet HMAC plus the source socket.
    # Position B: the wire TGID IS the canonical TGID (no OBP-edge re-number);
    # the talkgroup_slots table assigns the LOCAL timeslot and is the
    # fail-closed filter. See development/openbridge-hblink3-hblink4-design.md.
    # ------------------------------------------------------------------

    async def _start_openbridge(self, config: 'OpenBridgeConnectionConfig', loop) -> None:
        """Bind one OBP socket and register its state (one socket per OBP)."""
        try:
            infos = await loop.getaddrinfo(config.target_address, config.target_port,
                                           type=socket.SOCK_DGRAM)
            target_ip, target_port = infos[0][4][0], infos[0][4][1]
        except Exception as e:
            LOGGER.error(f'[OBP {config.name}] target DNS resolution failed: {e}')
            return
        try:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: OpenBridgeProtocol(self, config.name),
                local_addr=(config.local_address, config.local_port)
            )
        except Exception as e:
            LOGGER.error(f'[OBP {config.name}] failed to bind '
                         f'{config.local_address}:{config.local_port}: {e}')
            return
        state = OpenBridgeState(config=config, ip=target_ip, port=target_port, transport=transport)
        self._openbridges[config.name] = state
        for tgid in config.talkgroup_slots:
            self._obp_by_tgid[tgid] = config.name
        LOGGER.info(f'✓ OpenBridge "{config.name}" bound {config.local_address}:{config.local_port} '
                    f'→ {target_ip}:{target_port} ({len(config.talkgroup_slots)} talkgroups)')
        self._events.emit('openbridge_connected', self._openbridge_event_data(state))

    def _openbridge_event_data(self, state: 'OpenBridgeState') -> dict:
        """Dashboard payload describing an OBP trunk (name, peer, and TGID→TS map)."""
        cfg = state.config
        return {
            'connection_name': cfg.name,
            'network_id': cfg.network_id,
            'remote_address': cfg.target_address,
            'remote_port': state.port,
            'preserve_source_peer': cfg.preserve_source_peer,
            'talkgroups': {str(int.from_bytes(t, 'big')): ts
                           for t, ts in cfg.talkgroup_slots.items()},
        }

    @staticmethod
    def _obp_key(passphrase) -> bytes:
        """OBP HMAC key: passphrase as bytes."""
        return passphrase.encode('utf-8') if isinstance(passphrase, str) else passphrase

    def _handle_openbridge_packet(self, obp_name: str, packet: bytes, addr: tuple) -> None:
        """Authenticate, filter, and (TODO: route) an inbound OBP frame."""
        state = self._openbridges.get(obp_name)
        if state is None:
            return
        cfg = state.config

        # OBP carries only DMRD frames (53-byte DMRD + 20-byte HMAC-SHA1).
        if len(packet) != 73 or packet[:4] != DMRD:
            return
        dmrd = packet[:53]
        rx_hmac = packet[53:]
        calc = hmac_new(self._obp_key(cfg.passphrase), dmrd, sha1).digest()
        # Auth: HMAC must match AND the source socket must be the configured peer.
        if not compare_digest(rx_hmac, calc) or (addr[0], addr[1]) != (state.ip, state.port):
            LOGGER.debug(f'[OBP {obp_name}] frame discarded (HMAC or source mismatch)')
            return

        dst_id = dmrd[8:11]                 # canonical TGID (wire == canonical)
        local_ts = cfg.talkgroup_slots.get(dst_id)
        if local_ts is None:
            return                          # unmapped TGID -> dropped (fail-closed filter)

        rf_src = dmrd[5:8]
        peer_id = dmrd[11:15]
        stream_id = dmrd[16:20]

        # Normalize the DMR frame onto the assigned local timeslot. The OBP wire
        # slot is meaningless (forced to 1 by convention); HBlink4's core is
        # (TS,TGID)-keyed, so stamp the assigned TS into the slot bit (byte 15,
        # bit 7: 0=TS1, 1=TS2) before the frame enters routing.
        bits = (dmrd[15] | 0x80) if local_ts == 2 else (dmrd[15] & 0x7F)
        if bits != dmrd[15]:
            dmrd = dmrd[:15] + bytes([bits]) + dmrd[16:]

        source = ('openbridge', obp_name)
        now = time()
        frame_type = (bits & 0x30) >> 4
        is_term = self._is_dmr_terminator(dmrd, frame_type)

        stream = state.streams.get(stream_id)
        if stream is None:
            # New OBP stream: compute + cache targets once on (local_ts, canonical TGID).
            targets = self._calculate_stream_targets(source, local_ts, dst_id, stream_id, rf_src)
            stream = StreamState(
                repeater_id=peer_id, rf_src=rf_src, dst_id=dst_id, slot=local_ts,
                start_time=now, last_seen=now, stream_id=stream_id, packet_count=1,
                call_type='group', target_repeaters=targets, routing_cached=True,
            )
            state.streams[stream_id] = stream
            LOGGER.info(f'[OBP {obp_name}] RX stream start src={int.from_bytes(rf_src, "big")} '
                        f'tgid={int.from_bytes(dst_id, "big")} -> TS{local_ts} '
                        f'stream_id={stream_id.hex()} targets={len(targets)}')
            self._emit_stream_start('openbridge', obp_name, local_ts, rf_src, dst_id,
                                    stream_id, 'group')
        else:
            stream.last_seen = now
            stream.packet_count += 1

        # Forward through the shared path (per-target rewrite, reflection guard,
        # and OBP-target framing all live there — one routing path for all sources).
        self._forward_stream(dmrd, source, local_ts, rf_src, dst_id, stream_id)

        if is_term and not stream.ended:
            stream.ended = True
            stream.end_time = now
            LOGGER.info(f'[OBP {obp_name}] RX stream end stream_id={stream_id.hex()} '
                        f'packets={stream.packet_count}')
            self._emit_stream_end('openbridge', obp_name, local_ts, stream, 'terminator')

    def _obp_build_egress(self, state: 'OpenBridgeState', dmrd53: bytes,
                          source_peer_id: bytes) -> bytes:
        """Frame a DMRD as a 73-byte OBP packet for this OBP.

        - Truncated to a canonical 53-byte DMRD: HBP repeater sources deliver
          55-byte frames (trailing BER/RSSI bytes) which must be dropped so the
          wire frame is exactly 53 DMRD + 20 HMAC = 73; the peer computes its
          HMAC over 53 bytes and rejects anything longer.
        - RptrId (bytes 11:15): the true source peer when preserve_source_peer,
          else our network_id (Brandmeister-spec behavior).
        - Wire slot forced to 1 (OBP convention; the far end derives its own
          local TS from its talkgroup_slots table). Bit 0x80 is the slot-2 flag.
        - HMAC-SHA1(passphrase, DMRD) appended.
        """
        cfg = state.config
        rptr_id = source_peer_id if cfg.preserve_source_peer else cfg.network_id.to_bytes(4, 'big')
        bits = dmrd53[15] & ~0x80          # force slot 1 on the wire
        dmrd = b''.join([dmrd53[:11], rptr_id, bytes([bits]), dmrd53[16:53]])
        return dmrd + hmac_new(self._obp_key(cfg.passphrase), dmrd, sha1).digest()

    def _send_packet(self, data: bytes, addr: tuple):
        """Send packet to specified address"""
        cmd = data[:4]
        #if cmd != DMRD:  # Don't log DMR data packets
        #    LOGGER.debug(f'Sending {cmd.decode()} to {addr[0]}:{addr[1]}')
        # asyncio uses sendto() instead of write(data, addr)
        self.transport.sendto(data, normalize_addr(addr))

    def _send_nak(self, repeater_id: bytes, addr: tuple, reason: str = None, is_shutdown: bool = False):
        """Send NAK to specified address
        
        Args:
            repeater_id: The repeater's ID
            addr: The address to send the NAK to
            reason: Why the NAK is being sent
            is_shutdown: Whether this NAK is part of a graceful shutdown
        """
        log_level = logging.DEBUG if is_shutdown else logging.WARNING
        log_msg = f'Sending NAK to {addr[0]}:{addr[1]} for repeater {rid_to_int(repeater_id)}'
        if reason:
            log_msg += f' - {reason}'
        
        LOGGER.log(log_level, log_msg)
        self._send_packet(b''.join([MSTNAK, repeater_id]), addr)


# Logging functions moved to utils.py

# Configuration functions moved to config.py

def load_config(config_file: str):
    """Wrapper for config module load_config - maintains global CONFIG"""
    global CONFIG
    CONFIG = load_config_func(config_file, LOGGER)

def parse_outbound_connections() -> List[OutboundConnectionConfig]:
    """Wrapper for config module parse_outbound_connections"""
    return parse_outbound_func(CONFIG, LOGGER)

def parse_openbridge_connections() -> List[OpenBridgeConnectionConfig]:
    """Wrapper for config module parse_openbridge_connections"""
    return parse_openbridge_func(CONFIG, LOGGER)

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
                
                # Parse options to get talkgroups for dashboard
                slot1_tgs, slot2_tgs = primary_protocol._parse_options(config.options)
                
                # Emit initial connecting state event for dashboard
                primary_protocol._events.emit('outbound_connecting', {
                    'connection_name': config.name,
                    'radio_id': config.radio_id,
                    'remote_address': config.address,
                    'remote_port': config.port,
                    'slot1_talkgroups': list(slot1_tgs) if slot1_tgs else None,
                    'slot2_talkgroups': list(slot2_tgs) if slot2_tgs else None
                })
                
                # Start connection task
                task = asyncio.create_task(
                    primary_protocol._connect_outbound(config, loop),
                    name=f'outbound_{config.name}'
                )
                LOGGER.info(f'✓ Started outbound connection task for "{config.name}"')

    # Parse and start OpenBridge (OBP) trunk connections. One bound socket per
    # OBP; no login/keepalive. Only enabled OBPs are started (validation already
    # enforced one-OBP-per-TGID across enabled OBPs).
    openbridge_configs = parse_openbridge_connections()
    if openbridge_configs and protocols:
        primary_protocol = protocols[0]
        for config in openbridge_configs:
            if config.enabled:
                await primary_protocol._start_openbridge(config, loop)

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
    # Setup logging using the imported function
    global LOGGER
    LOGGER = setup_logging(CONFIG, __name__)
    
    # Deferred import — __version__ is bound on the package module after
    # __init__.py finishes, which is after hblink.py finishes loading.
    from hblink4 import __version__ as hblink4_version

    # Startup banner
    LOGGER.info('🚀 ═══════════════════════════════════════════════════════════════')
    LOGGER.info(f'🚀 HBLINK4 v{hblink4_version} STARTING UP')
    LOGGER.info('🚀 ═══════════════════════════════════════════════════════════════')
    
    asyncio.run(async_main())

if __name__ == '__main__':
    main()
