"""
Utility functions for HBlink4

This module contains standalone utility functions that don't depend on 
application state or class instances. These are pure functions that 
can be used throughout the codebase.
"""
import logging
import logging.handlers
import pathlib
from typing import Tuple, Union

# Type definitions for reusability
PeerAddress = Union[Tuple[str, int], Tuple[str, int, int, int]]


def safe_decode_bytes(data: bytes) -> str:
    """
    Safely decode bytes to UTF-8 string with error handling.
    Used for repeater metadata fields that may contain invalid UTF-8.
    
    Args:
        data: Bytes to decode
        
    Returns:
        Decoded and stripped string, or empty string if data is empty/None
    """
    if not data:
        return ''
    return data.decode('utf-8', errors='ignore').strip()


def normalize_addr(addr: PeerAddress) -> Tuple[str, int]:
    """
    Normalize address tuple to (ip, port) regardless of IPv4/IPv6 format.
    
    Args:
        addr: Address tuple - IPv4: (ip, port) or IPv6: (ip, port, flowinfo, scopeid)
        
    Returns:
        Normalized (ip, port) tuple
    """
    return (addr[0], addr[1])


def rid_to_int(repeater_id: bytes) -> int:
    """
    Convert repeater ID bytes to int.
    
    Args:
        repeater_id: 4-byte repeater ID
        
    Returns:
        Integer representation of repeater ID
    """
    return int.from_bytes(repeater_id, 'big')


def bytes_to_int(value: bytes) -> int:
    """
    Simple bytes to int conversion for logging and display purposes.
    
    Args:
        value: Bytes to convert
        
    Returns:
        Integer representation
    """
    return int.from_bytes(value, 'big')


# Default connection type detection patterns (used if not in config)
# These can be overridden in config.json under "connection_type_detection"
DEFAULT_HOTSPOT_PACKAGES = [
    'mmdvm_hs', 'dvmega', 'zumspot', 'jumbospot', 'nanodv',
    'openspot', 'dmo', 'simplex'
]

DEFAULT_NETWORK_PACKAGES = [
    'hblink', 'freedmr', 'brandmeister', 'xlx', 'dmr+', 'tgif', 'ipsc'
]

DEFAULT_REPEATER_PACKAGES = [
    'repeater', 'duplex', 'stm32', 'unknown'
]

DEFAULT_HOTSPOT_SOFTWARE = [
    'pi-star', 'pistar', 'ps4', 'wpsd'
]

DEFAULT_NETWORK_SOFTWARE = [
    'hblink', 'freedmr', 'brandmeister', 'xlx'
]


def detect_connection_type(software_id: bytes, package_id: bytes = None, config: dict = None) -> str:
    """
    Detect connection type based on package_id (primary) or software_id (fallback).
    
    Categories:
    - 'repeater': Full repeaters, club sites
    - 'hotspot': Personal hotspots (Pi-Star, WPSD, MMDVM_HS boards, simplex/DMO)
    - 'network': Network inbound connections (HBlink, FreeDMR, BrandMeister)
    - 'unknown': Unrecognized - defaults shown in "Other" section
    
    Args:
        software_id: Raw bytes from RPTC packet (40 bytes, null-padded)
        package_id: Raw bytes from RPTC packet (40 bytes, null-padded) - primary detection
        config: Optional config dict with connection_type_detection settings
        
    Returns:
        Connection type string: 'repeater', 'hotspot', 'network', or 'unknown'
    """
    # Load patterns from config or use defaults
    detection_config = (config or {}).get('connection_type_detection', {})
    
    hotspot_packages = detection_config.get('hotspot_packages', DEFAULT_HOTSPOT_PACKAGES)
    network_packages = detection_config.get('network_packages', DEFAULT_NETWORK_PACKAGES)
    repeater_packages = detection_config.get('repeater_packages', DEFAULT_REPEATER_PACKAGES)
    hotspot_software = detection_config.get('hotspot_software', DEFAULT_HOTSPOT_SOFTWARE)
    network_software = detection_config.get('network_software', DEFAULT_NETWORK_SOFTWARE)
    
    # Try package_id first (more reliable)
    if package_id:
        pkg_str = package_id.decode('utf-8', errors='ignore').strip().lower()
        
        if pkg_str:
            # Check network first (server connections)
            for network_pkg in network_packages:
                if network_pkg in pkg_str:
                    return 'network'
            
            # Check hotspot patterns
            for hotspot_pkg in hotspot_packages:
                if hotspot_pkg in pkg_str:
                    return 'hotspot'
            
            # Check repeater patterns
            for repeater_pkg in repeater_packages:
                if repeater_pkg in pkg_str:
                    return 'repeater'
            
            # Generic "MMDVM" without qualifiers - likely a repeater
            if pkg_str == 'mmdvm':
                return 'repeater'
    
    # Fallback to software_id if package_id didn't match
    if software_id:
        sw_str = software_id.decode('utf-8', errors='ignore').strip().lower()
        
        if sw_str:
            # Known network software
            if any(x in sw_str for x in network_software):
                return 'network'
            
            # Pi-Star/WPSD variants are typically hotspots
            if any(x in sw_str for x in hotspot_software):
                return 'hotspot'
    
    return 'unknown'


def cleanup_old_logs(log_dir: pathlib.Path, max_days: int, logger: logging.Logger = None) -> None:
    """
    Clean up log files older than max_days based on their date suffix.
    
    Args:
        log_dir: Directory containing log files
        max_days: Maximum age of logs to keep
        logger: Logger instance for output (optional)
    """
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
                    if logger:
                        logger.debug(f'Deleted old log file from {date_str}: {log_file}')
            except (OSError, ValueError) as e:
                if logger:
                    logger.warning(f'Error processing old log file {log_file}: {e}')
    except Exception as e:
        if logger:
            logger.error(f'Error during log cleanup: {e}')


def setup_logging(config: dict, logger_name: str = __name__) -> logging.Logger:
    """
    Configure logging with file and console handlers.
    
    Args:
        config: Logging configuration dictionary
        logger_name: Name for the logger
        
    Returns:
        Configured logger instance
    """
    logging_config = config.get('global', {}).get('logging', {})
    
    # Get logging configuration with defaults
    log_file = logging_config.get('file', 'logs/hblink.log')
    file_level = getattr(logging, logging_config.get('file_level', 'DEBUG'))
    console_level = getattr(logging, logging_config.get('console_level', 'INFO'))
    max_days = logging_config.get('retention_days', 30)
    
    log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # Create log directory if it doesn't exist
    log_path = pathlib.Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Get logger instance
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)  # Set to lowest level, handlers will filter
    
    # Clean up old log files
    cleanup_old_logs(log_path.parent, max_days, logger)
    
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
    
    file_handler.setLevel(file_level)
    file_handler.setFormatter(log_format)
    
    # Configure console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(log_format)
    
    # Add handlers if not already present
    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    
    return logger