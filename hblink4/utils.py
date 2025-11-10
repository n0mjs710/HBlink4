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