"""
Configuration loading and parsing for HBlink4

This module handles loading JSON configuration files and parsing
specific sections like outbound connections.
"""
import json
import logging
import sys
from typing import List, Dict, Any

# Import OutboundConnectionConfig from models module
try:
    from .models import OutboundConnectionConfig
except ImportError:
    # Fallback for when called from outside package
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from models import OutboundConnectionConfig


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
    required_global_fields = ['bind_ipv4', 'bind_port']
    
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