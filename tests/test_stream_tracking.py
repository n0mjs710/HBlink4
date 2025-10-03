#!/usr/bin/env python3
"""
Simple test to verify stream tracking functionality
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hblink4.hblink import StreamState, RepeaterState
from time import time, sleep

def test_stream_state():
    """Test StreamState is_active method"""
    print("Testing StreamState...")
    
    # Create a stream
    stream = StreamState(
        repeater_id=b'\x00\x04\xc3d',  # 312100
        rf_src=b'\x31\x21\x34',     # 3121234 (3 bytes)
        dst_id=b'\x00\x0c0',        # 3120 (3 bytes)
        slot=1,
        start_time=time(),
        last_seen=time(),
        stream_id=b'\xa1\xb2\xc3\xd4',
        packet_count=1
    )
    
    # Test active stream
    assert stream.is_active(2.0), "Stream should be active immediately"
    print("✓ Stream is active immediately after creation")
    
    # Wait and test still active
    sleep(0.5)
    assert stream.is_active(2.0), "Stream should still be active after 0.5s"
    print("✓ Stream is active after 0.5s")
    
    # Test inactive after timeout
    sleep(2.0)
    assert not stream.is_active(2.0), "Stream should be inactive after 2.5s total"
    print("✓ Stream is inactive after timeout")
    
    print("StreamState tests passed!\n")

def test_repeater_state():
    """Test RepeaterState slot management"""
    print("Testing RepeaterState...")
    
    # Create a repeater
    repeater = RepeaterState(
        repeater_id=b'\x00\x04\xc3d',  # 312100
        ip='192.168.1.100',
        port=62031
    )
    
    # Test empty slots
    assert repeater.get_slot_stream(1) is None, "Slot 1 should be empty"
    assert repeater.get_slot_stream(2) is None, "Slot 2 should be empty"
    print("✓ Both slots empty initially")
    
    # Create streams
    stream1 = StreamState(
        repeater_id=repeater.repeater_id,
        rf_src=b'\x31\x21\x34',
        dst_id=b'\x00\x0c0',
        slot=1,
        start_time=time(),
        last_seen=time(),
        stream_id=b'\xa1\xb2\xc3\xd4',
        packet_count=1
    )
    
    stream2 = StreamState(
        repeater_id=repeater.repeater_id,
        rf_src=b'\x31\x25\x78',
        dst_id=b'\x00\x0c1',
        slot=2,
        start_time=time(),
        last_seen=time(),
        stream_id=b'\xe1\xf2\x03\x14',
        packet_count=1
    )
    
    # Set streams
    repeater.set_slot_stream(1, stream1)
    repeater.set_slot_stream(2, stream2)
    
    # Test retrieval
    assert repeater.get_slot_stream(1) == stream1, "Should retrieve slot 1 stream"
    assert repeater.get_slot_stream(2) == stream2, "Should retrieve slot 2 stream"
    print("✓ Slots correctly store and retrieve streams")
    
    # Test clearing
    repeater.set_slot_stream(1, None)
    assert repeater.get_slot_stream(1) is None, "Slot 1 should be cleared"
    assert repeater.get_slot_stream(2) == stream2, "Slot 2 should remain"
    print("✓ Slots can be cleared independently")
    
    print("RepeaterState tests passed!\n")

def main():
    """Run all tests"""
    print("="*60)
    print("Stream Tracking Functionality Tests")
    print("="*60 + "\n")
    
    try:
        test_stream_state()
        test_repeater_state()
        
        print("="*60)
        print("All tests passed! ✓")
        print("="*60)
        return 0
        
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())
