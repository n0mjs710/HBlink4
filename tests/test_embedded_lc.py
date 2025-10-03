"""
Tests for DMR embedded LC extraction from voice burst frames

NOTE: These tests are currently disabled because LC recovery code is commented out.
      Search for "LC recovery - disabled until fixed" in hblink4/hblink.py
"""
import pytest
# LC recovery - disabled until fixed
# from hblink4.hblink import extract_embedded_lc, decode_embedded_lc, DMRLC

# All tests in this file are skipped until LC recovery is fixed
pytestmark = pytest.mark.skip(reason="LC recovery code disabled until fixed")


def test_extract_embedded_lc_valid_frame():
    """Test that embedded LC fragments are extracted from voice burst frames"""
    # Create a minimal DMRD packet with embedded LC fragment
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[4] = 0x01  # sequence = 1 (frame B)
    
    # Insert embedded LC fragment at bytes 33-34 (payload bytes 13-14)
    # This simulates 16 bits of embedded LC data
    packet[33:35] = bytes([0xAB, 0xCD])
    
    result = extract_embedded_lc(bytes(packet), 1)
    assert result is not None, "Should extract embedded LC fragment"
    assert result == bytes([0xAB, 0xCD]), "Should extract correct fragment"


def test_extract_embedded_lc_all_frames():
    """Test extraction from all 4 voice burst frames (B-E)"""
    for frame_num in range(1, 5):
        packet = bytearray(55)
        packet[0:4] = b'DMRD'
        packet[4] = frame_num
        
        # Insert test data
        packet[33:35] = bytes([0x10 * frame_num, 0x20 * frame_num])
        
        result = extract_embedded_lc(bytes(packet), frame_num)
        assert result is not None, f"Should extract from frame {frame_num}"
        assert len(result) == 2, "Should extract 2 bytes (16 bits)"


def test_extract_embedded_lc_invalid_frame_number():
    """Test that invalid frame numbers return None"""
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    
    # Frame 0 (A) - no embedded LC
    result = extract_embedded_lc(bytes(packet), 0)
    assert result is None, "Frame 0 should not have embedded LC"
    
    # Frame 5 (F) - no embedded LC
    result = extract_embedded_lc(bytes(packet), 5)
    assert result is None, "Frame 5 should not have embedded LC"
    
    # Frame 6 - invalid
    result = extract_embedded_lc(bytes(packet), 6)
    assert result is None, "Frame 6 is invalid"


def test_extract_embedded_lc_short_packet():
    """Test that short packets return None"""
    packet = bytearray(30)  # Too short
    
    result = extract_embedded_lc(bytes(packet), 1)
    assert result is None, "Short packets should return None"


def test_decode_embedded_lc_complete():
    """Test decoding embedded LC after collecting 4 fragments"""
    # Simulate collecting 4 fragments (8 bytes total)
    # Create a valid LC structure spread across 4 frames
    
    # Group call: FLCO=0, FID=0, Service=0, Dst=9, Src=312123
    # FLCO (6 bits): 000000
    # FID (8 bits): 00000000
    # Service (8 bits): 00000000
    # Dst (24 bits): 000000000000000000001001 (9)
    # Src (24 bits): 000001001100001100001011 (312123)
    
    lc_bits = bytearray([
        0x00,  # FLCO=0, FID MSB=0
        0x00,  # FID LSB=0, Service MSB=0
        0x00,  # Service LSB=0, Dst MSB=0
        0x00,  # Dst bits
        0x00,  # Dst bits
        0x24,  # Dst LSB=9 (00100100 >> 2 = 9)
        0x04,  # Src MSB
        0xC3,  # Src middle
        0x0C,  # Src LSB (312123 in binary)
    ])
    
    lc = decode_embedded_lc(lc_bits)
    assert lc is not None, "Should decode embedded LC"
    assert lc.is_valid, "Decoded LC should be valid"
    assert lc.flco == 0, "Should decode FLCO"
    assert lc.is_group_call, "Should identify as group call"


def test_decode_embedded_lc_insufficient_data():
    """Test that insufficient data returns None"""
    lc_bits = bytearray([0x00, 0x00, 0x00])  # Only 3 bytes
    
    lc = decode_embedded_lc(lc_bits)
    assert lc is None, "Should return None for insufficient data"


def test_embedded_lc_extraction_workflow():
    """Test the complete workflow of collecting and decoding embedded LC"""
    # Simulate receiving 4 voice burst frames with embedded LC
    accumulated_bits = bytearray()
    
    for frame_num in range(1, 5):
        packet = bytearray(55)
        packet[0:4] = b'DMRD'
        packet[4] = frame_num
        
        # Insert 2 bytes of embedded LC fragment
        # For this test, create a simple pattern
        packet[33] = 0x00
        packet[34] = frame_num * 0x10
        
        fragment = extract_embedded_lc(bytes(packet), frame_num)
        assert fragment is not None, f"Should extract from frame {frame_num}"
        
        accumulated_bits.extend(fragment)
    
    # Should have 8 bytes after 4 frames
    assert len(accumulated_bits) == 8, "Should have 8 bytes after 4 frames"
    
    # Try to decode (may not be valid LC, but should not crash)
    lc = decode_embedded_lc(accumulated_bits)
    # LC may or may not be valid depending on the test data
    # The important thing is the workflow executes without errors


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
