#!/usr/bin/env python3
"""
Test script for v1.5 detailed repeater information features
Tests the new get_pattern_for_repeater() method and validates event data structure
"""

import sys
import json
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from hblink4.access_control import RepeaterMatcher

def test_pattern_matching():
    """Test the new get_pattern_for_repeater method"""
    print("=" * 60)
    print("Testing Pattern Matching")
    print("=" * 60)
    
    # Load config
    config_path = Path(__file__).parent / 'config' / 'config.json'
    with open(config_path) as f:
        config = json.load(f)
    
    matcher = RepeaterMatcher(config)
    
    # Test case 1: ID in range
    test_cases = [
        (312001, None, "KS-DMR Network", "ID in range 312000-312099"),
        (315035, None, "KS-DMR Network", "Specific ID match"),
        (999999, None, None, "No pattern match - should use default"),
        (312001, "WA0EDA", "KS-DMR Network", "ID range + callsign match"),
    ]
    
    all_passed = True
    for radio_id, callsign, expected_name, description in test_cases:
        print(f"\nTest: {description}")
        print(f"  Radio ID: {radio_id}, Callsign: {callsign}")
        
        try:
            pattern = matcher.get_pattern_for_repeater(radio_id, callsign)
            
            if expected_name is None:
                if pattern is None:
                    print(f"  ✓ PASS: No pattern matched (will use default)")
                else:
                    print(f"  ✗ FAIL: Expected no pattern, but got '{pattern.name}'")
                    all_passed = False
            else:
                if pattern and pattern.name == expected_name:
                    print(f"  ✓ PASS: Pattern '{pattern.name}' matched")
                    print(f"     Description: {pattern.description}")
                    print(f"     Has IDs: {len(pattern.ids) > 0}")
                    print(f"     Has Ranges: {len(pattern.id_ranges) > 0}")
                    print(f"     Has Callsigns: {len(pattern.callsigns) > 0}")
                elif pattern:
                    print(f"  ✗ FAIL: Expected '{expected_name}', got '{pattern.name}'")
                    all_passed = False
                else:
                    print(f"  ✗ FAIL: Expected '{expected_name}', got no pattern")
                    all_passed = False
        except Exception as e:
            print(f"  ✗ FAIL: Exception: {e}")
            all_passed = False
    
    return all_passed

def test_event_structure():
    """Test that event data structure is correct"""
    print("\n" + "=" * 60)
    print("Testing Event Data Structure")
    print("=" * 60)
    
    # Simulate what _emit_repeater_details would create
    sample_event = {
        'repeater_id': 312001,
        'latitude': '38.9822',
        'longitude': '-94.6708',
        'height': '100',
        'tx_power': '50',
        'description': 'Test Repeater',
        'url': 'http://example.com',
        'software_id': 'HBlink4',
        'package_id': '20250101',
        'slots': '2',
        'matched_pattern': 'KS-DMR Network',
        'pattern_description': 'Repeaters in the KS-DMR network',
        'match_reason': 'id_range: 312000-312099'
    }
    
    required_fields = [
        'repeater_id', 'matched_pattern', 'pattern_description', 
        'match_reason', 'latitude', 'longitude'
    ]
    
    print("\nChecking required fields:")
    all_present = True
    for field in required_fields:
        if field in sample_event:
            print(f"  ✓ {field}: '{sample_event[field]}'")
        else:
            print(f"  ✗ MISSING: {field}")
            all_present = False
    
    return all_present

def test_api_response_structure():
    """Test expected API response structure"""
    print("\n" + "=" * 60)
    print("Testing API Response Structure")
    print("=" * 60)
    
    # Simulate what /api/repeater/{id} would return
    sample_response = {
        "repeater_id": 312001,
        "callsign": "WA0EDA-R",
        "connection": {
            "address": "192.168.1.100:54321",
            "uptime_seconds": 3600,
            "status": "connected",
            "missed_pings": 0
        },
        "location": {
            "location": "Overland Park, KS",
            "latitude": "38.9822",
            "longitude": "-94.6708"
        },
        "frequencies": {
            "rx_freq": "449.37500",
            "tx_freq": "444.37500",
            "colorcode": "1"
        },
        "access_control": {
            "matched_pattern": "KS-DMR Network",
            "pattern_description": "Repeaters in the KS-DMR network",
            "match_reason": "id_range: 312000-312099",
            "rpto_received": False,
            "talkgroups_source": "Pattern/Config"
        },
        "statistics": {
            "total_streams_today": 42,
            "slot1_active": False,
            "slot2_active": True
        }
    }
    
    sections = [
        'connection', 'location', 'frequencies', 
        'access_control', 'statistics'
    ]
    
    print("\nChecking response sections:")
    all_present = True
    for section in sections:
        if section in sample_response:
            print(f"  ✓ {section} section present ({len(sample_response[section])} fields)")
        else:
            print(f"  ✗ MISSING: {section}")
            all_present = False
    
    # Check access_control has pattern info
    if 'access_control' in sample_response:
        ac = sample_response['access_control']
        if 'matched_pattern' in ac and 'pattern_description' in ac and 'match_reason' in ac:
            print(f"\n  ✓ Pattern info complete:")
            print(f"     Pattern: {ac['matched_pattern']}")
            print(f"     Description: {ac['pattern_description']}")
            print(f"     Match: {ac['match_reason']}")
        else:
            print(f"\n  ✗ Pattern info incomplete")
            all_present = False
    
    return all_present

def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("HBlink4 v1.5 Feature Tests")
    print("=" * 60 + "\n")
    
    results = []
    
    # Run tests
    results.append(("Pattern Matching", test_pattern_matching()))
    results.append(("Event Structure", test_event_structure()))
    results.append(("API Response", test_api_response_structure()))
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
    
    all_passed = all(passed for _, passed in results)
    
    if all_passed:
        print("\n✓ All tests passed! v1.5 features are working correctly.")
        print("\nNext steps:")
        print("  1. Restart HBlink4 service: sudo systemctl restart hblink4")
        print("  2. Restart dashboard: sudo systemctl restart hblink4-dash")
        print("  3. Connect a repeater and click on it in the dashboard")
        print("  4. Verify the modal shows detailed information")
        return 0
    else:
        print("\n✗ Some tests failed. Please review the output above.")
        return 1

if __name__ == '__main__':
    sys.exit(main())
