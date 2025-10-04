"""
Protocol and system constants
"""

# HomeBrew Protocol Constants
DMRD    = b'DMRD'
MSTCL   = b'MSTCL'
MSTNAK  = b'MSTNAK' 
MSTN    = b'MSTN'
MSTC    = b'MSTC'
MSTPONG = b'MSTPONG'  # Server response to repeater's RPTPING/RPTP
RPTL    = b'RPTL'
RPTK    = b'RPTK'
RPTC    = b'RPTC'
RPTACK  = b'RPTACK'
RPTCL   = b'RPTCL'
RPTPING = b'RPTPING'  # Full command sent by repeater for keepalive
RPTP    = b'RPTP'     # Prefix used to identify RPTPING commands when parsing
RPTA    = b'RPTA'
RPTO    = b'RPTO'     # Repeater seding Options

# Protocol Configuration
DMR_DATA_PACKET_LENGTH = 55  # Minimum length of valid DMR data packet
DMR_PORT = 62031  # Default HomeBrew DMR port
DEFAULT_PING_TIME = 5.0  # Default ping interval in seconds
MAX_MISSED_PINGS = 3  # Maximum number of missed pings before disconnect

# DMR Sync Patterns (48 bits / 6 bytes)
# These patterns appear in the DMR payload at bytes 20-25 to identify frame types
# Used to distinguish between voice headers, terminators, and data frames

# Voice sync patterns (Base Station sourced)
DMR_SYNC_VOICE_HEADER = bytes.fromhex('755FD7DF75F7')    # Voice header with LC
DMR_SYNC_VOICE_TERM = bytes.fromhex('D5DD7DF75D55')      # Voice terminator with LC

# Data sync patterns (Base Station sourced)
DMR_SYNC_DATA_HEADER = bytes.fromhex('DFF57D75DF5D')     # Data header
DMR_SYNC_DATA_TERM = bytes.fromhex('7DFFD5F55D5F')       # Data terminator

# Mobile Station sourced patterns (less common in repeater systems)
DMR_SYNC_MS_VOICE_HEADER = bytes.fromhex('D5D7F77FD757')
DMR_SYNC_MS_VOICE_TERM = bytes.fromhex('77D57DD577F5')
DMR_SYNC_MS_DATA_HEADER = bytes.fromhex('7F7D5DD57DFD')
DMR_SYNC_MS_DATA_TERM = bytes.fromhex('55D5FDD755DF')
