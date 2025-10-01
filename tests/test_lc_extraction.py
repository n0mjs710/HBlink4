"""
Tests for DMR Link Control extraction
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hblink4.hblink import decode_lc, extract_voice_lc, DMRLC


def test_decode_lc_group_call():
    """Test decoding a group call LC"""
    # Sample LC bytes for a group call: FLCO=0, src=123456, dst=9
    # This is a simplified example - actual LC would be bit-packed
    lc_bytes = bytes([
        0x00,  # FLCO=0 (group call) in top 6 bits
        0x00, 0x00,  # FID and service options
        0x00, 0x00, 0x09,  # Destination ID = 9
        0x01, 0xE2, 0x40   # Source ID = 123456
    ])
    
    lc = decode_lc(lc_bytes)
    
    # Note: The actual bit packing is complex, this is a structural test
    assert lc.is_valid


def test_decode_lc_private_call():
    """Test decoding a private call LC"""
    lc_bytes = bytes([
        0x0C,  # FLCO=3 (private call) in top 6 bits
        0x00, 0x00,
        0x00, 0x00, 0x64,  # Destination ID = 100
        0x00, 0x00, 0xC8   # Source ID = 200
    ])
    
    lc = decode_lc(lc_bytes)
    assert lc.is_valid


def test_lc_properties():
    """Test LC property methods"""
    lc = DMRLC(flco=0, service_options=0x80, is_valid=True)
    assert lc.is_group_call
    assert not lc.is_private_call
    assert lc.is_emergency
    assert not lc.privacy_enabled
    
    lc2 = DMRLC(flco=3, service_options=0x40, is_valid=True)
    assert not lc2.is_group_call
    assert lc2.is_private_call
    assert not lc2.is_emergency
    assert lc2.privacy_enabled


def test_extract_voice_lc_short_packet():
    """Test that short packets return None"""
    short_packet = bytes(40)
    lc = extract_voice_lc(short_packet)
    assert lc is None


def test_extract_voice_lc_valid_packet():
    """Test extraction from a properly sized packet"""
    # Create a minimal valid DMRD packet (53 bytes)
    packet = bytearray(53)
    packet[0:4] = b'DMRD'
    
    # Add some LC data at the expected position (byte 38+)
    lc_start = 38
    packet[lc_start:lc_start+9] = bytes([
        0x00, 0x00, 0x00,
        0x00, 0x00, 0x09,
        0x01, 0xE2, 0x40
    ])
    
    lc = extract_voice_lc(bytes(packet))
    assert lc is not None
    assert lc.is_valid


if __name__ == '__main__':
    print("Running LC extraction tests...")
    
    test_decode_lc_group_call()
    print("✓ Group call LC decode")
    
    test_decode_lc_private_call()
    print("✓ Private call LC decode")
    
    test_lc_properties()
    print("✓ LC properties")
    
    test_extract_voice_lc_short_packet()
    print("✓ Short packet handling")
    
    test_extract_voice_lc_valid_packet()
    print("✓ Valid packet extraction")
    
    print("\nAll LC extraction tests passed!")
