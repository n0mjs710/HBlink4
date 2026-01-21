"""
HBlink4 - Next Generation DMR Server Protocol Handler

A complete architectural redesign of HBlink3, implementing a repeater-centric
approach to DMR server services. The HomeBrew DMR protocol is UDP-based, used for 
communication between DMR repeaters and servers.

License: GNU GPLv3
"""

from .hblink import main, HBProtocol, RepeaterState
from .constants import *

__version__ = '4.1.0'
__author__ = 'Cort Buffington, N0MJS'
__license__ = 'GNU GPLv3'

__all__ = [
    'main',
    'HBProtocol',
    'RepeaterState'
]
