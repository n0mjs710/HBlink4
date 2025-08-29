"""
HBlink4 - Next Generation DMR Master Server Protocol Handler

This package implements the HomeBrew DMR protocol, a UDP-based protocol
for communication between DMR repeaters and master servers.
"""

from .hblink import main
from .constants import *
from .models import RepeaterState, RepeaterConfig
from .protocol import HomeBrewProtocol
from .base_protocol import HomeBrewProtocolMixin

__version__ = '4.0.0'
__author__ = 'Cort Buffington, N0MJS'
__license__ = 'GNU GPLv3'

# Export main entry point
__all__ = [
    'main',
    'RepeaterState',
    'RepeaterConfig',
    'HomeBrewProtocol',
    'HomeBrewProtocolMixin'
]
