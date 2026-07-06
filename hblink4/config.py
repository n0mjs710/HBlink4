"""
Configuration loading and parsing for HBlink4

This module handles loading JSON configuration files and parsing
specific sections like outbound connections.
"""
import json
import logging
import sys
from typing import List, Dict, Any

# Import connection config models
try:
    from .models import OutboundConnectionConfig, OpenBridgeConnectionConfig
except ImportError:
    # Fallback for when called from outside package
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from models import OutboundConnectionConfig, OpenBridgeConnectionConfig


def load_config(config_file: str, logger: logging.Logger = None) -> Dict[str, Any]:
    """
    Load JSON configuration file.
    
    Args:
        config_file: Path to JSON configuration file
        logger: Logger instance for output (optional)
        
    Returns:
        Configuration dictionary
        
    Raises:
        SystemExit: If configuration cannot be loaded
    """
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
            if logger:
                logger.info(f'✓ Configuration loaded from {config_file}')
            return config
    except Exception as e:
        if logger:
            logger.error(f'✗ Error loading configuration from {config_file}: {e}')
        else:
            print(f'✗ Error loading configuration from {config_file}: {e}')
        sys.exit(1)


def parse_outbound_connections(config: Dict[str, Any], logger: logging.Logger = None) -> List:
    """
    Parse outbound connections from configuration dictionary.
    
    Args:
        config: Configuration dictionary
        logger: Logger instance for output (optional)
        
    Returns:
        List of OutboundConnectionConfig objects
        
    Raises:
        SystemExit: If required configuration fields are missing or invalid
    """
    # OutboundConnectionConfig is now imported at module level
    
    outbound_configs = []
    
    raw_outbounds = config.get('outbound_connections', [])
    if not raw_outbounds:
        if logger:
            logger.info('No outbound connections configured')
        return outbound_configs
    
    for idx, conn_dict in enumerate(raw_outbounds):
        try:
            config_obj = OutboundConnectionConfig(
                enabled=conn_dict.get('enabled', True),
                name=conn_dict['name'],
                address=conn_dict['address'],
                port=conn_dict['port'],
                radio_id=conn_dict['radio_id'],
                passphrase=conn_dict.get('passphrase', conn_dict.get('password', '')),  # Support both keys for backward compatibility
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
            outbound_configs.append(config_obj)
            if logger:
                logger.info(f'✓ Loaded outbound connection: {config_obj.name} → {config_obj.address}:{config_obj.port}')
        except KeyError as e:
            if logger:
                logger.error(f'✗ Outbound connection #{idx} missing required field: {e}')
            else:
                print(f'✗ Outbound connection #{idx} missing required field: {e}')
            sys.exit(1)
        except ValueError as e:
            if logger:
                logger.error(f'✗ Outbound connection #{idx} validation error: {e}')
            else:
                print(f'✗ Outbound connection #{idx} validation error: {e}')
            sys.exit(1)
    
    return outbound_configs


def parse_openbridge_connections(config: Dict[str, Any], logger: logging.Logger = None) -> List:
    """
    Parse OpenBridge (OBP) connections from the configuration dictionary.

    Each entry declares OBP transport plus a ``talkgroup_slots`` map — canonical
    TGID -> local timeslot — that serves as ownership, fail-closed filter, and
    slot assignment. Both the TGID key and the TS value are JSON strings (so the
    file reads consistently); they are parsed to a 3-byte TGID and an int TS here.

    Validation (fatal on failure):
      - TS must be 1 or 2; TGID must be in range.
      - one-TS-per-TGID is structural (map keys are unique).
      - one-OBP-per-TGID across all *enabled* OBPs (design §4.5) — a canonical
        TGID may be owned by only one OBP. Disabled OBPs are skipped, so a standby
        trunk may mirror the active trunk's TGIDs for manual failover.

    Returns:
        List of OpenBridgeConnectionConfig objects.

    Raises:
        SystemExit: on any missing field or validation error.
    """
    obp_configs = []

    raw_obps = config.get('openbridge_connections', [])
    if not raw_obps:
        if logger:
            logger.info('No OpenBridge connections configured')
        return obp_configs

    owner_of_tgid = {}  # tgid_bytes -> owning OBP name (enabled OBPs only)

    for idx, conn in enumerate(raw_obps):
        label = conn.get('name', f'#{idx}')
        try:
            # talkgroup_slots: "tgid" (str) -> "ts" (str)  =>  3-byte TGID -> int TS
            tg_slots = {}
            for tgid_str, ts_str in conn.get('talkgroup_slots', {}).items():
                tgid_int = int(tgid_str)
                ts = int(ts_str)
                if ts not in (1, 2):
                    raise ValueError(f"talkgroup {tgid_int} has invalid timeslot {ts} (must be 1 or 2)")
                if not (0 <= tgid_int <= 0xFFFFFF):
                    raise ValueError(f"talkgroup {tgid_int} out of range (0..16777215)")
                tg_slots[tgid_int.to_bytes(3, 'big')] = ts

            config_obj = OpenBridgeConnectionConfig(
                enabled=conn.get('enabled', True),
                name=conn['name'],
                network_id=conn['network_id'],
                local_address=conn.get('local_address', '0.0.0.0'),
                local_port=conn['local_port'],
                target_address=conn['target_address'],
                target_port=conn['target_port'],
                passphrase=conn.get('passphrase', conn.get('password', '')),
                talkgroup_slots=tg_slots,
                preserve_source_peer=conn.get('preserve_source_peer', True),
                description=conn.get('description', ''),
            )

            # one-OBP-per-TGID across enabled OBPs (design §4.5)
            if config_obj.enabled:
                for tgid_bytes in config_obj.talkgroup_slots:
                    prior = owner_of_tgid.get(tgid_bytes)
                    if prior is not None and prior != config_obj.name:
                        raise ValueError(
                            f"talkgroup {int.from_bytes(tgid_bytes, 'big')} is claimed by both "
                            f"OpenBridge '{prior}' and '{config_obj.name}' — each TGID may belong to only one OBP")
                    owner_of_tgid[tgid_bytes] = config_obj.name

            obp_configs.append(config_obj)
            if logger:
                state = 'enabled' if config_obj.enabled else 'disabled'
                logger.info(f'✓ Loaded OpenBridge: {config_obj.name} → '
                            f'{config_obj.target_address}:{config_obj.target_port} '
                            f'({len(config_obj.talkgroup_slots)} talkgroups, {state})')
        except KeyError as e:
            msg = f'✗ OpenBridge connection {label} missing required field: {e}'
            logger.error(msg) if logger else print(msg)
            sys.exit(1)
        except ValueError as e:
            msg = f'✗ OpenBridge connection {label} validation error: {e}'
            logger.error(msg) if logger else print(msg)
            sys.exit(1)

    return obp_configs


def validate_config(config: Dict[str, Any], logger: logging.Logger = None) -> bool:
    """
    Validate configuration structure and required fields.
    
    Args:
        config: Configuration dictionary to validate
        logger: Logger instance for output (optional)
        
    Returns:
        True if configuration is valid, False otherwise
    """
    required_sections = ['global']
    required_global_fields = ['bind_ipv4', 'port_ipv4']
    
    # Check required sections
    for section in required_sections:
        if section not in config:
            if logger:
                logger.error(f'✗ Missing required configuration section: {section}')
            return False
    
    # Check required global fields
    global_config = config.get('global', {})
    for field in required_global_fields:
        if field not in global_config:
            if logger:
                logger.error(f'✗ Missing required global configuration field: {field}')
            return False
    
    if logger:
        logger.info('✓ Configuration validation passed')
    return True