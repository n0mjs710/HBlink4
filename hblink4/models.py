"""
Common data models used throughout HBlink4
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union, Tuple
from datetime import datetime
from time import time
from random import randint

# Type definitions
RadioID = Union[bytes, str]
RepeaterAddress = Tuple[str, int]  # (IP, Port)

@dataclass
class RepeaterState:
    """Data class for storing repeater state"""
    radio_id: RadioID
    ip: str
    port: int
    registered: bool = False
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
    def address(self) -> RepeaterAddress:
        """Get repeater's address tuple"""
        return (self.ip, self.port)
