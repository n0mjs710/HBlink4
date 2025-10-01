#!/usr/bin/env python3
"""
Test hang time functionality for stream tracking
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hblink4.hblink import StreamState, RepeaterState
from time import time, sleep

def test_hang_time():
    """Test stream hang time logic"""
    print("Testing Stream Hang Time...")
    
    # Create a stream
    stream = StreamState(
        radio_id=b'\x00\x04\xc3d',  # 312100
        rf_src=b'\x31\x21\x34',     # 3121234 (3 bytes)
        dst_id=b'\x00\x0c0',        # 3120 (3 bytes)
        slot=1,
        start_time=time(),
        last_seen=time(),
        stream_id=b'\xa1\xb2\xc3\xd4',
        packet_count=10,
        ended=False
    )
    
    # Test 1: Active stream, not ended
    assert stream.is_active(2.0), "Stream should be active"
    assert not stream.is_in_hang_time(2.0, 3.0), "Active stream should not be in hang time"
    print("✓ Active stream is not in hang time")
    
    # Wait for stream to timeout
    sleep(2.1)
    
    # Test 2: Stream timed out but not marked as ended
    assert not stream.is_active(2.0), "Stream should be inactive after timeout"
    assert not stream.is_in_hang_time(2.0, 3.0), "Unmarked stream should not be in hang time"
    print("✓ Timed out stream (not marked ended) is not in hang time")
    
    # Test 3: Mark stream as ended - now it should be in hang time
    stream.ended = True
    assert stream.is_in_hang_time(2.0, 3.0), "Ended stream should be in hang time"
    print("✓ Ended stream is in hang time")
    
    # Test 4: Wait for hang time to expire
    sleep(3.1)  # Total: 5.2s (2.1s timeout + 3.1s hang time)
    assert not stream.is_in_hang_time(2.0, 3.0), "Stream should be out of hang time"
    print("✓ Stream exits hang time after configured duration")
    
    # Test 5: Create new stream to test hang time during active transmission
    stream2 = StreamState(
        radio_id=b'\x00\x04\xc3d',
        rf_src=b'\x31\x25\x78',     # Different source
        dst_id=b'\x00\x0c1',
        slot=1,
        start_time=time(),
        last_seen=time(),
        stream_id=b'\xb1\xc2\xd3\xe4',
        packet_count=1,
        ended=False
    )
    
    # Active stream should never be in hang time
    assert not stream2.is_in_hang_time(2.0, 3.0), "Active stream cannot be in hang time"
    print("✓ New active stream is not in hang time")
    
    print("Stream hang time tests passed!\n")

def test_hang_time_edge_cases():
    """Test edge cases for hang time"""
    print("Testing Hang Time Edge Cases...")
    
    current = time()
    
    # Test 1: Stream at exactly timeout boundary
    stream = StreamState(
        radio_id=b'\x00\x04\xc3d',
        rf_src=b'\x31\x21\x34',
        dst_id=b'\x00\x0c0',
        slot=1,
        start_time=current - 3.0,
        last_seen=current - 2.0,  # Exactly at timeout
        stream_id=b'\xa1\xb2\xc3\xd4',
        packet_count=10,
        ended=True
    )
    
    assert stream.is_in_hang_time(2.0, 3.0), "Should be in hang time at boundary"
    print("✓ Hang time starts exactly at timeout boundary")
    
    # Test 2: Stream at exactly hang time expiry
    stream.last_seen = current - 5.0  # 2.0s timeout + 3.0s hang = 5.0s total
    assert not stream.is_in_hang_time(2.0, 3.0), "Should be out of hang time at expiry"
    print("✓ Hang time ends exactly at expiry boundary")
    
    # Test 3: Different timeout and hang time values
    stream.last_seen = current - 3.5
    stream.ended = True
    assert stream.is_in_hang_time(3.0, 2.0), "Should work with different timeout/hang values"
    print("✓ Hang time works with custom timeout values")
    
    # Test 4: Zero hang time
    stream.last_seen = current - 2.1
    stream.ended = True
    assert not stream.is_in_hang_time(2.0, 0.0), "Zero hang time should immediately expire"
    print("✓ Zero hang time expires immediately")
    
    print("Hang time edge case tests passed!\n")

def main():
    """Run all hang time tests"""
    print("="*60)
    print("Stream Hang Time Functionality Tests")
    print("="*60 + "\n")
    
    try:
        test_hang_time()
        test_hang_time_edge_cases()
        
        print("="*60)
        print("All hang time tests passed! ✓")
        print("="*60)
        return 0
        
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())
