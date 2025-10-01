"""
Tests for DMR terminator frame detection
"""
import pytest
from hblink4.hblink import HBProtocol


def test_voice_terminator_detection():
    """Test that voice terminator frames are correctly identified"""
    protocol = HBProtocol()
    
    # Create a minimal DMRD packet with voice terminator sync pattern
    # Structure: DMRD (4) + seq (1) + src (3) + dst (3) + radio_id (4) + bits (1) + stream_id (4) + payload (33+)
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[4] = 0x00  # sequence
    packet[5:8] = bytes([0x00, 0x00, 0x01])  # source
    packet[8:11] = bytes([0x00, 0x00, 0x09])  # destination (TG 9)
    packet[11:15] = bytes([0x00, 0x31, 0x20, 0x00])  # radio_id
    packet[15] = 0xC0  # bits: slot 2 (0x80) + group call (0x40) + voice sync (0x10)
    packet[16:20] = bytes([0xAA, 0xBB, 0xCC, 0xDD])  # stream_id
    
    # Insert voice terminator sync pattern at bytes 20-25
    packet[20:26] = bytes.fromhex('D5DD7DF75D55')
    
    frame_type = 0x01  # Voice sync
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is True, "Should detect voice terminator sync pattern"


def test_voice_header_not_terminator():
    """Test that voice header frames are NOT identified as terminators"""
    protocol = HBProtocol()
    
    # Create a minimal DMRD packet with voice header sync pattern
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[4] = 0x00  # sequence
    packet[5:8] = bytes([0x00, 0x00, 0x01])  # source
    packet[8:11] = bytes([0x00, 0x00, 0x09])  # destination (TG 9)
    packet[11:15] = bytes([0x00, 0x31, 0x20, 0x00])  # radio_id
    packet[15] = 0xD0  # bits: slot 2 + group call + voice sync
    packet[16:20] = bytes([0xAA, 0xBB, 0xCC, 0xDD])  # stream_id
    
    # Insert voice header sync pattern at bytes 20-25
    packet[20:26] = bytes.fromhex('755FD7DF75F7')
    
    frame_type = 0x01  # Voice sync
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is False, "Should NOT detect voice header as terminator"


def test_voice_frame_not_sync():
    """Test that regular voice frames (not sync) are not terminators"""
    protocol = HBProtocol()
    
    # Create a minimal DMRD packet with regular voice frame
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[4] = 0x00  # sequence
    packet[5:8] = bytes([0x00, 0x00, 0x01])  # source
    packet[8:11] = bytes([0x00, 0x00, 0x09])  # destination (TG 9)
    packet[11:15] = bytes([0x00, 0x31, 0x20, 0x00])  # radio_id
    packet[15] = 0x80  # bits: slot 2 + voice frame (0x00)
    packet[16:20] = bytes([0xAA, 0xBB, 0xCC, 0xDD])  # stream_id
    
    frame_type = 0x00  # Regular voice frame (not sync)
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is False, "Regular voice frames should not be terminators"


def test_short_packet_not_terminator():
    """Test that packets too short to contain sync pattern return False"""
    protocol = HBProtocol()
    
    # Create a packet that's too short
    packet = bytearray(20)
    frame_type = 0x01  # Voice sync
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is False, "Short packets should not be detected as terminators"


def test_data_sync_not_implemented():
    """Test that data sync frames currently return False (not implemented)"""
    protocol = HBProtocol()
    
    # Create a minimal DMRD packet with data sync
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[15] = 0xA0  # bits: slot 2 + data sync (0x20)
    
    frame_type = 0x02  # Data sync
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is False, "Data terminators not yet implemented"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
