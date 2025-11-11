"""
DMR Protocol handling for HBlink4

This module contains DMR protocol parsing, validation, and packet handling
functions that can be used independently of the main protocol class.
"""
from typing import Dict, Any, Optional


def parse_dmr_packet(data: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse DMR packet fields into a dictionary.
    Hot path function - used for every voice packet.
    
    Args:
        data: Raw packet data (should be at least 55 bytes)
        
    Returns:
        Dict with parsed fields or None if invalid packet
        
    Packet structure:
        - bytes 0-3: Homebrew header
        - byte 4: Sequence number  
        - bytes 5-7: RF source ID (3 bytes)
        - bytes 8-10: Destination ID (3 bytes) 
        - bytes 11-14: Repeater ID (4 bytes)
        - byte 15: Bits field (slot, call type, frame type)
        - bytes 16-19: Stream ID (4 bytes)
        - bytes 20-52: DMR payload (33 bytes)
    """
    if len(data) < 55:
        return None
        
    return {
        'seq': data[4],
        'rf_src': data[5:8],
        'dst_id': data[8:11],
        'repeater_id': data[11:15],
        'bits': data[15],
        'stream_id': data[16:20],
        'slot': 2 if (data[15] & 0x80) else 1,
        'call_type': (data[15] & 0x40) >> 6,
        'frame_type': (data[15] & 0x30) >> 4,
        'src_id_int': int.from_bytes(data[5:8], 'big'),
        'dst_id_int': int.from_bytes(data[8:11], 'big'),
        'repeater_id_int': int.from_bytes(data[11:15], 'big')
    }


def is_dmr_terminator(data: bytes, frame_type: int) -> bool:
    """
    Determine if a DMR packet is a stream terminator by checking the frame type.
    
    In the Homebrew protocol, terminators are indicated in byte 15 of the packet:
    - Bits 4-5 (_frame_type): Must be 0x2 (HBPF_DATA_SYNC - data sync frame)
    - Bits 0-3 (_dtype_vseq): Must be 0x2 (HBPF_SLT_VTERM - voice terminator)
    
    This is much simpler than ETSI sync pattern extraction, as the Homebrew
    protocol explicitly flags terminator frames in the packet header.
    
    Args:
        data: The full DMRD packet (including 20-byte Homebrew header + 33-byte DMR data)
        frame_type: The frame type extracted from byte 15, bits 4-5
                   (0 = voice, 1 = voice sync, 2 = data sync)
    
    Returns:
        bool: True if this is a terminator frame, False otherwise
    
    Note:
        This enables immediate terminator detection (~60ms latency) instead of
        timeout-based detection (~200ms). HBlink3 uses this same method.
    """
    # Check packet length
    if len(data) < 16:
        return False
        
    # Extract the data type / voice sequence from bits 0-3 of byte 15
    _bits = data[15]
    _dtype_vseq = _bits & 0xF
    
    # Check if it's a data sync frame with voice terminator
    # frame_type == 2 means HBPF_DATA_SYNC (data sync frame)
    # _dtype_vseq == 2 means HBPF_SLT_VTERM (voice terminator within the superframe)
    return frame_type == 2 and _dtype_vseq == 2


def validate_packet_length(data: bytes, expected_min: int = 55) -> bool:
    """
    Validate that packet meets minimum length requirements.
    
    Args:
        data: Packet data to validate
        expected_min: Minimum expected length in bytes
        
    Returns:
        bool: True if packet is long enough, False otherwise
    """
    return len(data) >= expected_min


def extract_packet_command(data: bytes) -> bytes:
    """
    Extract command from packet data.
    
    Args:
        data: Packet data (should be at least 4 bytes)
        
    Returns:
        bytes: Command bytes (first 4 bytes) or empty bytes if too short
    """
    if len(data) < 4:
        return b''
    return data[:4]


def extract_repeater_id(data: bytes) -> bytes:
    """
    Extract repeater ID from packet data.
    
    Args:
        data: Packet data (should be at least 15 bytes for DMR packets)
        
    Returns:
        bytes: Repeater ID (4 bytes) or empty bytes if packet too short
    """
    if len(data) < 15:
        return b''
    return data[11:15]


def get_call_type_name(call_type: int) -> str:
    """
    Convert call type integer to human-readable name.
    
    Args:
        call_type: Call type from packet (0 or 1)
        
    Returns:
        str: "group" for group calls, "private" for private calls
    """
    return "group" if call_type == 0 else "private"


def get_slot_name(slot: int) -> str:
    """
    Convert slot number to human-readable name.
    
    Args:
        slot: Slot number (1 or 2)
        
    Returns:
        str: "TS1" or "TS2" for timeslot names
    """
    return f"TS{slot}"


def format_id_display(id_bytes: bytes) -> str:
    """
    Format ID bytes for display purposes.
    
    Args:
        id_bytes: ID as bytes (typically 3 or 4 bytes)
        
    Returns:
        str: Formatted ID as decimal string
    """
    if not id_bytes:
        return "0"
    return str(int.from_bytes(id_bytes, 'big'))


def create_packet_summary(packet: Dict[str, Any]) -> str:
    """
    Create a human-readable summary of a parsed packet.
    
    Args:
        packet: Parsed packet dictionary from parse_dmr_packet()
        
    Returns:
        str: Human-readable packet summary
    """
    if not packet:
        return "Invalid packet"
    
    return (f"Slot {packet['slot']}: "
            f"SRC={packet['src_id_int']} → "
            f"DST={packet['dst_id_int']} "
            f"({get_call_type_name(packet['call_type'])}), "
            f"seq={packet['seq']}, "
            f"frame_type={packet['frame_type']}")