"""
Tests for DMR terminator frame detection using Homebrew Protocol (HBP) flags
"""
import pytest
from hblink4.hblink import HBProtocol


def test_voice_terminator_detection():
    """Test that voice terminator frames are correctly identified via HBP flags"""
    protocol = HBProtocol()
    
    # Create a minimal DMRD packet with HBP terminator flags
    # Structure: DMRD (4) + seq (1) + src (3) + dst (3) + radio_id (4) + bits (1) + stream_id (4) + payload (33+)
    # 
    # In HBP, terminators are indicated in byte 15:
    # - Bits 4-5: frame_type = 0x2 (DATA_SYNC)
    # - Bits 0-3: dtype_vseq = 0x2 (SLT_VTERM - voice terminator)
    #
    # Byte 15 calculation: (0x2 << 4) | 0x2 = 0x22
    # Plus slot bit (0x80 for slot 2) and group call (0x40) = 0xE2
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[4] = 0x00  # sequence
    packet[5:8] = bytes([0x00, 0x00, 0x01])  # source
    packet[8:11] = bytes([0x00, 0x00, 0x09])  # destination (TG 9)
    packet[11:15] = bytes([0x00, 0x31, 0x20, 0x00])  # radio_id
    packet[15] = 0xE2  # bits: slot 2 (0x80) + group call (0x40) + DATA_SYNC (0x20) + SLT_VTERM (0x02)
    packet[16:20] = bytes([0xAA, 0xBB, 0xCC, 0xDD])  # stream_id
    
    frame_type = 0x02  # DATA_SYNC (bits 4-5 of byte 15)
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is True, "Should detect voice terminator via HBP flags"


def test_voice_header_not_terminator():
    """Test that voice header frames are NOT identified as terminators"""
    protocol = HBProtocol()
    
    # Create a minimal DMRD packet with voice header (not terminator)
    # Voice header has DATA_SYNC (0x2) but dtype_vseq = 0x1 (SLT_VHEAD), not 0x2
    # Byte 15: slot 2 (0x80) + group call (0x40) + DATA_SYNC (0x20) + SLT_VHEAD (0x01) = 0xE1
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[4] = 0x00  # sequence
    packet[5:8] = bytes([0x00, 0x00, 0x01])  # source
    packet[8:11] = bytes([0x00, 0x00, 0x09])  # destination (TG 9)
    packet[11:15] = bytes([0x00, 0x31, 0x20, 0x00])  # radio_id
    packet[15] = 0xE1  # bits: slot 2 + group call + DATA_SYNC + SLT_VHEAD
    packet[16:20] = bytes([0xAA, 0xBB, 0xCC, 0xDD])  # stream_id
    
    frame_type = 0x02  # DATA_SYNC
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is False, "Should NOT detect voice header as terminator"


def test_voice_frame_not_sync():
    """Test that regular voice frames (not sync) are not terminators"""
    protocol = HBProtocol()
    
    # Create a minimal DMRD packet with regular voice frame (frame_type = 0x0)
    # Byte 15: slot 2 (0x80) + group call (0x40) + VOICE (0x00) + vseq (0x01) = 0xC1
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[4] = 0x00  # sequence
    packet[5:8] = bytes([0x00, 0x00, 0x01])  # source
    packet[8:11] = bytes([0x00, 0x00, 0x09])  # destination (TG 9)
    packet[11:15] = bytes([0x00, 0x31, 0x20, 0x00])  # radio_id
    packet[15] = 0xC1  # bits: slot 2 + group call + VOICE frame + vseq 1
    packet[16:20] = bytes([0xAA, 0xBB, 0xCC, 0xDD])  # stream_id
    
    frame_type = 0x00  # Regular voice frame (not DATA_SYNC)
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is False, "Regular voice frames should not be terminators"


def test_short_packet_not_terminator():
    """Test that packets too short to read byte 15 don't cause errors"""
    protocol = HBProtocol()
    
    # Create a packet that's too short (less than 16 bytes)
    packet = bytearray(10)
    frame_type = 0x02  # DATA_SYNC
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is False, "Short packets should not be detected as terminators"


def test_data_terminator():
    """Test that data terminators are detected (frame_type=2, dtype_vseq=2)"""
    protocol = HBProtocol()
    
    # Create a data terminator packet
    # Byte 15: slot 2 (0x80) + group call (0x40) + DATA_SYNC (0x20) + SLT_VTERM (0x02) = 0xE2
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[15] = 0xE2  # DATA_SYNC + SLT_VTERM
    
    frame_type = 0x02  # DATA_SYNC
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is True, "Data terminator should be detected"


def test_wrong_dtype_vseq():
    """Test that DATA_SYNC frames with wrong dtype_vseq are not terminators"""
    protocol = HBProtocol()
    
    # Create a DATA_SYNC packet but with dtype_vseq = 0x3 (not terminator)
    # Byte 15: slot 2 (0x80) + group call (0x40) + DATA_SYNC (0x20) + other (0x03) = 0xE3
    packet = bytearray(55)
    packet[0:4] = b'DMRD'
    packet[15] = 0xE3  # DATA_SYNC but dtype_vseq != 0x2
    
    frame_type = 0x02  # DATA_SYNC
    
    result = protocol._is_dmr_terminator(bytes(packet), frame_type)
    assert result is False, "DATA_SYNC with wrong dtype_vseq should not be terminator"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
