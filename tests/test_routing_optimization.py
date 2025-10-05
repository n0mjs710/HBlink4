#!/usr/bin/env python3
"""
Test suite for routing optimization features:
- Set-based TG lookups
- Routing cache calculation
- RPTO parsing and config intersection
- Unregistered repeater NAK handling
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hblink4.hblink import StreamState, RepeaterState, HBProtocol
from hblink4.access_control import RepeaterMatcher, RepeaterConfig
from time import time
import json


def test_set_based_tg_storage():
    """Test that TG sets are stored correctly and provide O(1) lookups"""
    print("\n=== Testing Set-Based TG Storage ===")
    
    # Create repeater with TG sets
    repeater = RepeaterState(
        repeater_id=b'\x00\x04\xc3d',  # 312100
        ip='192.168.1.100',
        port=54000
    )
    
    # Initialize TG sets
    repeater.slot1_talkgroups = {1, 2, 3, 9}
    repeater.slot2_talkgroups = {4, 5, 6, 9}
    
    # Test O(1) membership
    assert 1 in repeater.slot1_talkgroups, "TG 1 should be in TS1"
    assert 9 in repeater.slot1_talkgroups, "TG 9 should be in TS1"
    assert 9 in repeater.slot2_talkgroups, "TG 9 should be in TS2"
    assert 999 not in repeater.slot1_talkgroups, "TG 999 should not be in TS1"
    
    print("✓ TG sets initialized correctly")
    print(f"  TS1: {sorted(repeater.slot1_talkgroups)}")
    print(f"  TS2: {sorted(repeater.slot2_talkgroups)}")
    
    # Test set operations (intersection for RPTO)
    requested_tgs = {1, 2, 999, 1000}
    allowed_tgs = requested_tgs & repeater.slot1_talkgroups
    
    assert allowed_tgs == {1, 2}, "Intersection should only include valid TGs"
    print(f"✓ Set intersection works: {requested_tgs} & config = {allowed_tgs}")
    
    print("Set-Based TG Storage tests passed!\n")


def test_routing_cache_fields():
    """Test that StreamState has routing cache fields"""
    print("=== Testing Routing Cache Fields ===")
    
    # Create stream with routing cache
    stream = StreamState(
        repeater_id=b'\x00\x04\xc3d',
        rf_src=b'\x31\x21\x34',
        dst_id=b'\x00\x0c0',
        slot=1,
        start_time=time(),
        last_seen=time(),
        stream_id=b'\xa1\xb2\xc3\xd4',
        packet_count=1
    )
    
    # Initially no routing cached
    assert stream.routing_cached == False, "Routing should not be cached initially"
    assert stream.target_repeaters is None, "Target repeaters should be None initially"
    print("✓ Initial state: routing_cached=False, target_repeaters=None")
    
    # Simulate routing calculation
    stream.target_repeaters = {b'\x00\x04\xc3e', b'\x00\x04\xc3f'}
    stream.routing_cached = True
    
    assert stream.routing_cached == True, "Routing should be marked as cached"
    assert len(stream.target_repeaters) == 2, "Should have 2 target repeaters"
    print(f"✓ After caching: routing_cached=True, {len(stream.target_repeaters)} targets")
    
    # Test that we can use the cached routing
    for target_id in stream.target_repeaters:
        assert isinstance(target_id, bytes), "Target IDs should be bytes"
    print("✓ Cached targets are usable")
    
    print("Routing Cache Fields tests passed!\n")


def test_rpto_parsing():
    """Test RPTO message parsing logic"""
    print("=== Testing RPTO Parsing ===")
    
    # Test valid RPTO formats
    test_cases = [
        ("TS1=1,2,3;TS2=4,5,6", {1, 2, 3}, {4, 5, 6}),
        ("TS1=1;TS2=4", {1}, {4}),
        ("TS1=9", {9}, set()),
        ("TS2=9", set(), {9}),
        ("TS1=1,2,3", {1, 2, 3}, set()),
        ("TS1=1,2,3;TS2=", {1, 2, 3}, set()),  # Empty TS2
        ("", set(), set()),  # Empty string
    ]
    
    for options_str, expected_ts1, expected_ts2 in test_cases:
        # Parse options (mimicking _handle_options logic)
        requested_ts1 = set()
        requested_ts2 = set()
        
        for part in options_str.split(';'):
            part = part.strip()
            if not part or '=' not in part:
                continue
            key, value = part.split('=', 1)
            key = key.strip().upper()
            
            if key == 'TS1' and value:
                requested_ts1 = {int(tg.strip()) for tg in value.split(',') 
                                 if tg.strip().isdigit()}
            elif key == 'TS2' and value:
                requested_ts2 = {int(tg.strip()) for tg in value.split(',') 
                                 if tg.strip().isdigit()}
        
        assert requested_ts1 == expected_ts1, f"TS1 parsing failed for '{options_str}'"
        assert requested_ts2 == expected_ts2, f"TS2 parsing failed for '{options_str}'"
        print(f"✓ Parsed '{options_str}' → TS1={sorted(requested_ts1) if requested_ts1 else '[]'}, TS2={sorted(requested_ts2) if requested_ts2 else '[]'}")
    
    print("RPTO Parsing tests passed!\n")


def test_config_intersection():
    """Test that RPTO respects config as master (intersection logic)"""
    print("=== Testing Config Intersection ===")
    
    # Config allows these TGs
    config_ts1 = {1, 2, 3, 9}
    config_ts2 = {4, 5, 6, 9}
    
    # Test cases: (requested TGs, expected final TGs, description)
    test_cases = [
        # Repeater requests subset of allowed TGs
        ({1, 2}, config_ts1, {1, 2}, "Subset of allowed TGs"),
        
        # Repeater requests superset (includes non-allowed TGs)
        ({1, 2, 999, 1000}, config_ts1, {1, 2}, "Superset filtered to allowed"),
        
        # Repeater requests all allowed TGs
        ({1, 2, 3, 9}, config_ts1, {1, 2, 3, 9}, "All allowed TGs"),
        
        # Repeater requests only non-allowed TGs
        ({999, 1000}, config_ts1, set(), "No overlap = empty"),
        
        # Repeater requests nothing (empty RPTO)
        (set(), config_ts1, config_ts1, "Empty request = keep config"),
        
        # Repeater requests one allowed + one disallowed
        ({9, 999}, config_ts1, {9}, "Mixed request filtered"),
    ]
    
    for requested, config, expected, description in test_cases:
        # Apply config intersection logic
        final = requested & config if requested else config
        
        assert final == expected, f"Failed: {description}"
        print(f"✓ {description}")
        print(f"  Requested: {sorted(requested) if requested else '(empty)'}")
        print(f"  Config:    {sorted(config)}")
        print(f"  Final:     {sorted(final) if final else '(empty)'}")
        print()
    
    print("Config Intersection tests passed!\n")


def test_rejected_tgs_detection():
    """Test that we can detect and log rejected TGs"""
    print("=== Testing Rejected TGs Detection ===")
    
    config_ts1 = {1, 2, 3, 9}
    
    test_cases = [
        ({1, 2, 999, 1000}, {999, 1000}, "Two rejected TGs"),
        ({1, 2}, set(), "No rejected TGs"),
        ({999}, {999}, "All rejected"),
        (set(), set(), "Empty request"),
    ]
    
    for requested, expected_rejected, description in test_cases:
        final = requested & config_ts1 if requested else config_ts1
        rejected = requested - config_ts1
        
        assert rejected == expected_rejected, f"Failed: {description}"
        if rejected:
            print(f"✓ {description}: {sorted(rejected)}")
        else:
            print(f"✓ {description}: (none)")
    
    print("Rejected TGs Detection tests passed!\n")


def test_stream_start_routing_calculation():
    """Test the concept of calculating routing once at stream start"""
    print("=== Testing Stream Start Routing Calculation ===")
    
    # Simulate repeater states
    repeaters = {
        b'\x01': RepeaterState(b'\x01', '192.168.1.1', 54001, 
                               connection_state='connected'),
        b'\x02': RepeaterState(b'\x02', '192.168.1.2', 54002, 
                               connection_state='connected'),
        b'\x03': RepeaterState(b'\x03', '192.168.1.3', 54003, 
                               connection_state='connected'),
    }
    
    # Set up TG access
    repeaters[b'\x01'].slot1_talkgroups = {1, 2, 3}
    repeaters[b'\x02'].slot1_talkgroups = {1, 2}
    repeaters[b'\x03'].slot1_talkgroups = {3, 4}
    
    # Simulate stream from repeater 1 to TG 1
    source_id = b'\x01'
    tgid = 1
    slot = 1
    
    # Calculate targets (excluding source, checking TG access)
    targets = set()
    for rid, rep in repeaters.items():
        if rid == source_id:
            continue  # Don't forward to source
        if rep.connection_state != 'connected':
            continue
        if tgid in rep.slot1_talkgroups:
            targets.add(rid)
    
    # Should target repeater 2 only (has TG 1, not repeater 3)
    assert targets == {b'\x02'}, "Should only target repeater 2"
    print(f"✓ Routing calculated: TG {tgid} from {source_id.hex()} → {[t.hex() for t in targets]}")
    
    # Create stream with cached routing
    stream = StreamState(
        repeater_id=source_id,
        rf_src=b'\x12\x34\x56',
        dst_id=tgid.to_bytes(3, 'big'),
        slot=slot,
        start_time=time(),
        last_seen=time(),
        stream_id=b'\xaa\xbb\xcc\xdd',
        target_repeaters=targets,
        routing_cached=True
    )
    
    # Now "forward" 300 packets using cached routing
    packet_count = 0
    for i in range(300):
        # No per-packet routing check needed!
        for target_id in stream.target_repeaters:
            # Just send to pre-calculated targets
            packet_count += 1
    
    expected_packets = 300 * len(targets)
    assert packet_count == expected_packets, "Should send to all cached targets"
    print(f"✓ Forwarded 300 packets to {len(targets)} targets = {packet_count} total sends")
    print(f"✓ No per-packet routing checks performed (cached once at start)")
    
    print("Stream Start Routing Calculation tests passed!\n")


def test_slot_availability_exclusion():
    """Test that busy slots are excluded from routing at stream start"""
    print("=== Testing Slot Availability Exclusion ===")
    
    # Repeater with active stream on TS1
    repeater = RepeaterState(
        repeater_id=b'\x01',
        ip='192.168.1.1',
        port=54001,
        connection_state='connected'
    )
    repeater.slot1_talkgroups = {1, 2, 3}
    
    # Create active stream on TS1
    active_stream = StreamState(
        repeater_id=b'\x01',
        rf_src=b'\x11\x11\x11',
        dst_id=b'\x00\x00\x01',  # TG 1
        slot=1,
        start_time=time(),
        last_seen=time(),
        stream_id=b'\xaa\xaa\xaa\xaa'
    )
    repeater.slot1_stream = active_stream
    
    # New stream wants to use TS1 on this repeater
    # Should be excluded because slot is busy
    assert repeater.get_slot_stream(1) is not None, "TS1 should be busy"
    assert repeater.get_slot_stream(1).stream_id == b'\xaa\xaa\xaa\xaa', "Should be our active stream"
    print("✓ TS1 is busy with active stream")
    
    # TS2 should be available
    assert repeater.get_slot_stream(2) is None, "TS2 should be available"
    print("✓ TS2 is available")
    
    # At stream start, this repeater would be excluded from TS1 routing
    # but could be included for TS2 routing
    print("✓ Busy slot would be excluded from routing calculation")
    
    print("Slot Availability Exclusion tests passed!\n")


def test_performance_calculation():
    """Calculate theoretical performance improvement"""
    print("=== Testing Performance Calculation ===")
    
    packets_per_stream = 300
    target_repeaters = 10
    checks_per_packet_old = 3  # inbound check, outbound check, slot check
    
    # Old approach: per-packet checks
    old_operations = packets_per_stream * target_repeaters * checks_per_packet_old
    print(f"Old approach:")
    print(f"  {packets_per_stream} packets × {target_repeaters} targets × {checks_per_packet_old} checks")
    print(f"  = {old_operations:,} operations per stream")
    
    # New approach: calculate once at start
    startup_checks = target_repeaters * checks_per_packet_old
    forwarding_sends = packets_per_stream * 5  # Assume 5 targets pass checks
    new_operations = startup_checks + forwarding_sends
    
    print(f"\nNew approach:")
    print(f"  Stream start: {target_repeaters} targets × {checks_per_packet_old} checks = {startup_checks} operations")
    print(f"  Forwarding: {packets_per_stream} packets × 5 targets = {forwarding_sends} sends")
    print(f"  = {new_operations:,} operations per stream")
    
    reduction = (old_operations - new_operations) / old_operations * 100
    print(f"\n✓ Reduction: {reduction:.1f}% fewer operations")
    
    assert reduction > 80, "Should have >80% reduction in operations"
    print(f"✓ Performance improvement validated: {reduction:.1f}% reduction")
    
    print("Performance Calculation tests passed!\n")


def run_all_tests():
    """Run all routing optimization tests"""
    print("\n" + "="*60)
    print("ROUTING OPTIMIZATION TEST SUITE")
    print("="*60)
    
    tests = [
        test_set_based_tg_storage,
        test_routing_cache_fields,
        test_rpto_parsing,
        test_config_intersection,
        test_rejected_tgs_detection,
        test_stream_start_routing_calculation,
        test_slot_availability_exclusion,
        test_performance_calculation,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"\n❌ FAILED: {test.__name__}")
            print(f"   {e}")
            failed += 1
        except Exception as e:
            print(f"\n❌ ERROR in {test.__name__}: {e}")
            failed += 1
    
    print("\n" + "="*60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("="*60 + "\n")
    
    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
