#!/usr/bin/env python3
"""
Tests for DMR Talker Alias extraction and decoding
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hblink4.hblink import (
    DMRLC, decode_lc, extract_talker_alias, decode_talker_alias
)


def test_talker_alias_header_detection():
    """Test that FLCO=4 is detected as talker alias header"""
    lc = DMRLC(flco=4, is_valid=True)
    assert lc.is_talker_alias_header
    assert not lc.is_talker_alias_block
    assert not lc.is_group_call


def test_talker_alias_block_detection():
    """Test that FLCO=5,6,7 are detected as talker alias blocks"""
    for flco in [5, 6, 7]:
        lc = DMRLC(flco=flco, is_valid=True)
        assert lc.is_talker_alias_block
        assert not lc.is_talker_alias_header
        assert not lc.is_group_call


def test_extract_talker_alias_header():
    """Test extracting talker alias header with format and length"""
    # FLCO=4, service_options contains format (bits 0-1) and length (bits 2-7)
    # Format 0 (7-bit ASCII), Length 8 bytes
    lc = DMRLC(
        flco=4,
        service_options=0b00100000,  # format=0, length=8 (8 << 2)
        dst_id=0x4E304D,  # "N0M" in ASCII
        src_id=0x4A5300,  # "JS\0"
        fid=0x00,
        is_valid=True
    )
    
    data = b'\x00' * 53  # Dummy DMRD packet
    result = extract_talker_alias(lc, data)
    
    assert result is not None
    format_type, length, data_bytes = result
    assert format_type == 0  # 7-bit ASCII
    assert length == 8
    assert len(data_bytes) == 7
    # Check first 3 bytes are "N0M"
    assert data_bytes[:3] == b'N0M'


def test_extract_talker_alias_block():
    """Test extracting talker alias data block"""
    # FLCO=5 (block 1)
    lc = DMRLC(
        flco=5,
        dst_id=0x20436F,  # " Co"
        src_id=0x727400,  # "rt\0"
        fid=0x00,
        is_valid=True
    )
    
    data = b'\x00' * 53
    result = extract_talker_alias(lc, data)
    
    assert result is not None
    block_num, _, data_bytes = result
    assert block_num == 1  # FLCO=5 -> block 1
    assert len(data_bytes) == 7


def test_decode_talker_alias_7bit_ascii():
    """Test decoding 7-bit ASCII talker alias"""
    blocks = {
        0: b'N0MJS\x00\x00',  # Header block with "N0MJS" + padding
        1: b' Cort\x00\x00',  # Block 1
    }
    
    alias = decode_talker_alias(format_type=0, length=10, blocks=blocks)
    assert alias is not None
    assert 'N0MJS' in alias
    assert 'Co' in alias  # May be trimmed to length 10


def test_decode_talker_alias_iso88591():
    """Test decoding ISO-8859-1 (Latin-1) talker alias"""
    # ISO-8859-1 allows extended characters
    blocks = {
        0: b'Test\xE9\x00\x00',  # "Testé" with accented e
        1: b'User\x00\x00\x00',
    }
    
    alias = decode_talker_alias(format_type=1, length=9, blocks=blocks)
    assert alias is not None
    assert 'Test' in alias


def test_decode_talker_alias_utf8():
    """Test decoding UTF-8 talker alias"""
    # UTF-8 can encode any Unicode character
    test_string = "Test™"
    test_bytes = test_string.encode('utf-8')
    
    blocks = {
        0: test_bytes[:7] if len(test_bytes) >= 7 else test_bytes + b'\x00' * (7 - len(test_bytes)),
        1: test_bytes[7:14] if len(test_bytes) > 7 else b'\x00' * 7,
    }
    
    alias = decode_talker_alias(format_type=2, length=len(test_bytes), blocks=blocks)
    assert alias is not None


def test_decode_talker_alias_utf16():
    """Test decoding UTF-16BE talker alias"""
    # UTF-16BE uses 2 bytes per character
    test_string = "Test"
    test_bytes = test_string.encode('utf-16-be')
    
    blocks = {
        0: test_bytes[:7] if len(test_bytes) >= 7 else test_bytes + b'\x00' * (7 - len(test_bytes)),
        1: test_bytes[7:14] if len(test_bytes) > 7 else b'\x00' * 7,
    }
    
    alias = decode_talker_alias(format_type=3, length=len(test_bytes), blocks=blocks)
    assert alias is not None
    assert 'Test' in alias


def test_decode_talker_alias_length_trimming():
    """Test that alias is trimmed to specified length"""
    blocks = {
        0: b'1234567',
        1: b'890ABCD',
        2: b'EFGHIJK',
        3: b'LMNOPQR',
    }
    
    # Request only first 10 bytes
    alias = decode_talker_alias(format_type=0, length=10, blocks=blocks)
    assert alias is not None
    # Should be trimmed to 10 characters (7-bit ASCII, one char per byte)
    assert len(alias.strip()) <= 10


def test_decode_talker_alias_padding_removal():
    """Test that null bytes and spaces are stripped from alias"""
    blocks = {
        0: b'TEST\x00\x00\x00',
        1: b'\x00\x00\x00\x00\x00\x00\x00',
    }
    
    alias = decode_talker_alias(format_type=0, length=7, blocks=blocks)
    assert alias is not None
    assert alias == 'TEST'


def test_decode_talker_alias_empty_blocks():
    """Test that empty blocks dict returns None"""
    alias = decode_talker_alias(format_type=0, length=10, blocks={})
    assert alias is None


def test_decode_talker_alias_missing_blocks():
    """Test decoding with some missing blocks"""
    # Only have blocks 0 and 2, missing block 1
    blocks = {
        0: b'START\x00\x00',
        2: b'END\x00\x00\x00\x00',
    }
    
    alias = decode_talker_alias(format_type=0, length=14, blocks=blocks)
    assert alias is not None
    # Should still decode what we have


def test_extract_non_talker_alias_lc():
    """Test that non-talker-alias LC returns None"""
    lc = DMRLC(flco=0, is_valid=True)  # Group call, not talker alias
    data = b'\x00' * 53
    
    result = extract_talker_alias(lc, data)
    assert result is None


if __name__ == '__main__':
    # Run tests
    test_talker_alias_header_detection()
    test_talker_alias_block_detection()
    test_extract_talker_alias_header()
    test_extract_talker_alias_block()
    test_decode_talker_alias_7bit_ascii()
    test_decode_talker_alias_iso88591()
    test_decode_talker_alias_utf8()
    test_decode_talker_alias_utf16()
    test_decode_talker_alias_length_trimming()
    test_decode_talker_alias_padding_removal()
    test_decode_talker_alias_empty_blocks()
    test_decode_talker_alias_missing_blocks()
    test_extract_non_talker_alias_lc()
    
    print("All talker alias tests passed!")
