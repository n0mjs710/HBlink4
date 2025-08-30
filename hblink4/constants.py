"""
Protocol and system constants
"""

# HomeBrew Protocol Constants
DMRD    = b'DMRD'
MSTCL   = b'MSTCL'
MSTNAK  = b'MSTNAK'
MSTN    = b'MSTN'
MSTP    = b'MSTP'
MSTC    = b'MSTC'
RPTL    = b'RPTL'
RPTK    = b'RPTK'
RPTC    = b'RPTC'
RPTACK  = b'RPTACK'
RPTCL   = b'RPTCL'
RPTP    = b'RPTP'  # Repeater ping (keepalive) message
RPTA    = b'RPTA'

# Protocol Configuration
DMR_DATA_PACKET_LENGTH = 55  # Minimum length of valid DMR data packet
DMR_PORT = 62031  # Default HomeBrew DMR port
DEFAULT_PING_TIME = 5.0  # Default ping interval in seconds
MAX_MISSED_PINGS = 3  # Maximum number of missed pings before disconnect
