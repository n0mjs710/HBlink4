"""
Unit tests for the access_control module
"""

import unittest
import logging
from hblink4.access_control import (
    RepeaterMatcher, RepeaterConfig, InvalidPatternError, BlacklistError
)

# Configure logging to show detailed test information
logging.basicConfig(level=logging.INFO,
                   format='%(message)s')

class TestRepeaterMatcher(unittest.TestCase):
    def setUp(self):
        """Load test configuration from the sample config file"""
        import json
        import os

        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                                 'config', 'config_sample.json')
        
        with open(config_path, 'r') as f:
            full_config = json.load(f)
            
        # Include both repeater and blacklist configurations
        self.config = {
            "repeaters": full_config["repeater_configurations"],
            "blacklist": full_config["blacklist"]
        }
        
        # Invalid configuration with multiple match types
        self.invalid_config = {
            "repeaters": {
                "patterns": [
                    {
                        "name": "Invalid Multiple Matches",
                        "match": {
                            "ids": [312100],
                            "callsigns": ["WA0EDA*"]
                        },
                        "config": {
                            "enabled": True,
                            "timeout": 20,
                            "passphrase": "invalid",
                            "talkgroups": [3120],
                            "description": "Invalid Config"
                        }
                    }
                ],
                "default": full_config["repeater_configurations"]["default"]
            }
        }
        
        logging.info("\n=== Test Configuration Loaded ===")
        logging.info("Testing with patterns from config file:")
        for pattern in self.config["repeaters"]["patterns"]:
            logging.info(f"\nPattern: {pattern['name']}")
            logging.info(f"Match criteria: {pattern['match']}")
            logging.info(f"Configuration: {pattern['config']}")
        self.matcher = RepeaterMatcher(self.config)

    def _format_config_section(self, section: dict) -> str:
        """Helper to format a config section for display"""
        import json
        return json.dumps(section, indent=2)

    def test_multiple_match_types(self):
        """Test that configuration with multiple match types is now supported"""
        logging.info("\n=== Testing Multiple Match Types Support ===")
        logging.info("Creating matcher with multiple match types in one pattern:")
        # Update invalid_config to remove 'enabled' field
        valid_multi_config = {
            "repeaters": {
                "patterns": [
                    {
                        "name": "Multi-Match Pattern",
                        "match": {
                            "ids": [312100],
                            "callsigns": ["WA0EDA*"]
                        },
                        "config": {
                            "passphrase": "multi-match-key",
                            "slot1_talkgroups": [3120],
                            "slot2_talkgroups": [3121]
                        }
                    }
                ],
                "default": self.config["repeaters"]["default"]
            }
        }
        logging.info(self._format_config_section(valid_multi_config["repeaters"]["patterns"][0]))
        
        # Should not raise an error
        matcher = RepeaterMatcher(valid_multi_config)
        
        # Test that it matches on ID
        config = matcher.get_repeater_config(312100, "OTHER")
        self.assertEqual(config.passphrase, "multi-match-key")
        logging.info(f"Result: Matched on ID 312100")
        
        # Test that it matches on callsign
        config = matcher.get_repeater_config(999999, "WA0EDA-1")
        self.assertEqual(config.passphrase, "multi-match-key")
        logging.info(f"Result: Matched on callsign WA0EDA-1")

    def test_specific_id_match(self):
        """Test matching a specific radio ID from the Club Network"""
        logging.info("\n=== Testing Specific Radio ID Match ===")
        radio_id = 312100  # First ID in Club Network, but also in KS-DMR range
        callsign = "WA0EDA-TEST"
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        logging.info("Note: This ID is in the KS-DMR range (312000-312099), which appears first in config")
        
        matching_pattern = next(p for p in self.config["repeaters"]["patterns"] 
                              if p["name"] == "KS-DMR Network")
        logging.info(f"Matching configuration section:\n{self._format_config_section(matching_pattern)}")
        
        config = self.matcher.get_repeater_config(radio_id, callsign)
        logging.info(f"Result: MATCHED KS-DMR range (first pattern in order)")
        logging.info(f"Configuration applied:")
        logging.info(f"- Passphrase: {config.passphrase}")
        logging.info(f"- Slot 1 TGs: {config.slot1_talkgroups}")
        logging.info(f"- Slot 2 TGs: {config.slot2_talkgroups}")
        
        # Since KS-DMR Network pattern comes first and matches, we get its config
        self.assertEqual(config.passphrase, "ks-dmr-network-key")
        self.assertEqual(config.slot1_talkgroups, [8, 9])
        self.assertEqual(config.slot2_talkgroups, [3120, 3121, 3122])

    def test_id_range_match(self):
        """Test matching a radio ID within KS-DMR range"""
        logging.info("\n=== Testing ID Range Match ===")
        radio_id = 312050  # Middle of KS-DMR range
        callsign = "WA0EDA"  # Should be ignored because ID range match takes precedence
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        
        matching_pattern = next(p for p in self.config["repeaters"]["patterns"] 
                              if p["match"].get("id_ranges"))
        logging.info(f"Matching configuration section:\n{self._format_config_section(matching_pattern)}")
        
        config = self.matcher.get_repeater_config(radio_id, callsign)
        logging.info(f"Result: MATCHED within KS-DMR range (ignoring callsign)")
        logging.info(f"Configuration applied:")
        logging.info(f"- Passphrase: {config.passphrase}")
        logging.info(f"- Slot 1 TGs: {config.slot1_talkgroups}")
        logging.info(f"- Slot 2 TGs: {config.slot2_talkgroups}")
        
        self.assertEqual(config.passphrase, "ks-dmr-network-key")
        self.assertEqual(config.slot1_talkgroups, [8, 9])
        self.assertEqual(config.slot2_talkgroups, [3120, 3121, 3122])

    def test_callsign_match(self):
        """Test matching WA0EDA callsign pattern"""
        logging.info("\n=== Testing Callsign Match ===")
        radio_id = 999999  # ID that doesn't match any specific or range patterns
        callsign = "WA0EDA-1"
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        
        matching_pattern = next(p for p in self.config["repeaters"]["patterns"] 
                              if p["match"].get("callsigns"))
        logging.info(f"Matching configuration section:\n{self._format_config_section(matching_pattern)}")
        
        config = self.matcher.get_repeater_config(radio_id, callsign)
        logging.info(f"Result: MATCHED WA0EDA callsign pattern")
        logging.info(f"Configuration applied:")
        logging.info(f"- Passphrase: {config.passphrase}")
        logging.info(f"- Slot 1 TGs: {config.slot1_talkgroups}")
        logging.info(f"- Slot 2 TGs: {config.slot2_talkgroups}")
        
        self.assertEqual(config.passphrase, "wa0eda-network-key")
        self.assertEqual(config.slot1_talkgroups, [8])
        self.assertEqual(config.slot2_talkgroups, [31201, 31202])

    def test_default_config(self):
        """Test falling back to default configuration"""
        logging.info("\n=== Testing Default Configuration Fallback ===")
        radio_id = 999999
        callsign = "KB1ABC"
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        logging.info("No specific rules should match this combination")
        
        logging.info(f"Default configuration section:\n{self._format_config_section(self.config['repeaters']['default'])}")
        
        config = self.matcher.get_repeater_config(radio_id, callsign)
        logging.info(f"Result: NO MATCH - using default configuration")
        logging.info(f"Configuration applied:")
        logging.info(f"- Passphrase: {config.passphrase}")
        logging.info(f"- Slot 1 TGs: {config.slot1_talkgroups}")
        logging.info(f"- Slot 2 TGs: {config.slot2_talkgroups}")
        
        self.assertEqual(config.passphrase, "passw0rd")
        self.assertEqual(config.slot1_talkgroups, [1])
        self.assertEqual(config.slot2_talkgroups, [2])

    def test_match_priority(self):
        """Test that pattern order determines priority (first match wins)"""
        logging.info("\n=== Testing Pattern Order Priority ===")
        
        # Test: ID in range matches first pattern
        radio_id = 312100  # Matches both KS-DMR range (first) AND Club Network IDs (later)
        callsign = "WA0EDA"  # Also matches WA0EDA pattern
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        logging.info("This matches multiple patterns:")
        logging.info("  1. KS-DMR Network (id_range 312000-312099) - FIRST")
        logging.info("  2. WA0EDA Repeaters (callsign WA0EDA*)")
        logging.info("  3. Regional Network (id_range includes 312000-312999)")
        logging.info("  4. Club Network (ids includes 312100)")
        
        config = self.matcher.get_repeater_config(radio_id, callsign)
        logging.info(f"Result: MATCHED first pattern (KS-DMR Network)")
        logging.info(f"Using passphrase: {config.passphrase}")
        self.assertEqual(config.passphrase, "ks-dmr-network-key")  # First matching pattern
        
        # Test: ID outside all ranges, matches callsign
        radio_id = 999999  # Doesn't match any ID patterns
        logging.info(f"\nTesting repeater - ID: {radio_id}, Callsign: {callsign}")
        logging.info("This ID matches no ID patterns, only callsign pattern")
        
        config = self.matcher.get_repeater_config(radio_id, callsign)
        logging.info(f"Result: MATCHED WA0EDA callsign pattern")
        logging.info(f"Using passphrase: {config.passphrase}")
        self.assertEqual(config.passphrase, "wa0eda-network-key")  # WA0EDA pattern

    def test_blacklist_specific_id(self):
        """Test that blacklisted IDs are rejected"""
        logging.info("\n=== Testing Blacklist Specific ID ===")
        radio_id = 1
        callsign = "TEST"
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        
        with self.assertRaises(BlacklistError) as context:
            self.matcher.get_repeater_config(radio_id, callsign)
        
        logging.info(f"Result: Correctly rejected - {str(context.exception)}")
        self.assertEqual(context.exception.pattern_name, "Blocked IDs")
        self.assertEqual(context.exception.reason, "Repeated abuse of network")

    def test_blacklist_range(self):
        """Test that IDs in blacklisted ranges are rejected"""
        logging.info("\n=== Testing Blacklist Range ===")
        radio_id = 315123
        callsign = "TEST"
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        
        with self.assertRaises(BlacklistError) as context:
            self.matcher.get_repeater_config(radio_id, callsign)
            
        logging.info(f"Result: Correctly rejected - {str(context.exception)}")
        self.assertEqual(context.exception.pattern_name, "Blocked Range")
        self.assertEqual(context.exception.reason, "Unauthorized DMR-MARC range")

    def test_blacklist_callsign(self):
        """Test that blacklisted callsigns are rejected"""
        logging.info("\n=== Testing Blacklist Callsign ===")
        radio_id = 123456
        callsign = "BADACTOR123"
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        
        with self.assertRaises(BlacklistError) as context:
            self.matcher.get_repeater_config(radio_id, callsign)
            
        logging.info(f"Result: Correctly rejected - {str(context.exception)}")
        self.assertEqual(context.exception.pattern_name, "Blocked Callsigns")
        self.assertEqual(context.exception.reason, "Network abuse")

if __name__ == '__main__':
    unittest.main()
