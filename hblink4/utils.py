"""Utility functions for HBlink4."""

import signal
import logging
import asyncio
from typing import Callable

LOGGER = logging.getLogger(__name__)

def setup_graceful_shutdown(cleanup_callback: Callable) -> None:
    """Set up signal handlers for graceful shutdown.
    
    Args:
        cleanup_callback: Function to call for cleanup before shutdown
    """
    def signal_handler(signum, frame):
        """Handle shutdown signals by calling cleanup and then exiting."""
        signame = signal.Signals(signum).name
        LOGGER.info(f"Received shutdown signal {signame}")
        cleanup_callback()
        LOGGER.info("Cleanup complete, exiting...")

    # Register handlers for SIGINT (Ctrl+C) and SIGTERM
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
