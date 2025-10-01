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

    def test_invalid_multiple_match_types(self):
        """Test that configuration with multiple match types is rejected"""
        logging.info("\n=== Testing Invalid Configuration (Multiple Match Types) ===")
        logging.info("Attempting to create matcher with invalid configuration:")
        logging.info(self._format_config_section(self.invalid_config["repeaters"]["patterns"][0]))
        
        with self.assertRaises(InvalidPatternError) as context:
            RepeaterMatcher(self.invalid_config)
        
        logging.info(f"Result: Correctly rejected with error: {str(context.exception)}")

    def test_specific_id_match(self):
        """Test matching a specific radio ID from the Club Network"""
        logging.info("\n=== Testing Specific Radio ID Match ===")
        radio_id = 312100  # First ID in Club Network
        callsign = "WA0EDA-TEST"  # Should be ignored because ID match takes precedence
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        
        matching_pattern = next(p for p in self.config["repeaters"]["patterns"] 
                              if p["match"].get("ids"))
        logging.info(f"Matching configuration section:\n{self._format_config_section(matching_pattern)}")
        
        config = self.matcher.get_repeater_config(radio_id, callsign)
        logging.info(f"Result: MATCHED specific ID (ignoring callsign)")
        logging.info(f"Configuration applied:")
        logging.info(f"- Description: {config.description}")
        logging.info(f"- Timeout: {config.timeout}s")
        logging.info(f"- Passphrase: {config.passphrase}")
        logging.info(f"- Talkgroups: {config.talkgroups}")
        
        self.assertEqual(config.timeout, 30)
        self.assertEqual(config.description, "Club Network Repeater")
        self.assertEqual(config.passphrase, "club-network-key")
        self.assertEqual(config.talkgroups, [3100, 3101, 3102])

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
        logging.info(f"- Description: {config.description}")
        logging.info(f"- Timeout: {config.timeout}s")
        logging.info(f"- Passphrase: {config.passphrase}")
        logging.info(f"- Talkgroups: {config.talkgroups}")
        
        self.assertEqual(config.timeout, 30)
        self.assertEqual(config.description, "KS-DMR Network Repeater")
        self.assertEqual(config.passphrase, "ks-dmr-network-key")
        self.assertEqual(config.talkgroups, [3120, 3121, 3122])

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
        logging.info(f"- Description: {config.description}")
        logging.info(f"- Timeout: {config.timeout}s")
        logging.info(f"- Passphrase: {config.passphrase}")
        logging.info(f"- Talkgroups: {config.talkgroups}")
        
        self.assertEqual(config.timeout, 30)
        self.assertEqual(config.description, "WA0EDA Club Repeater")
        self.assertEqual(config.passphrase, "wa0eda-network-key")
        self.assertEqual(config.talkgroups, [31201, 31202])

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
        logging.info(f"- Description: {config.description}")
        logging.info(f"- Timeout: {config.timeout}s")
        logging.info(f"- Passphrase: {config.passphrase}")
        logging.info(f"- Talkgroups: {config.talkgroups}")
        
        self.assertEqual(config.timeout, 30)
        self.assertEqual(config.description, "Default Repeater Configuration")
        self.assertEqual(config.passphrase, "passw0rd")
        self.assertEqual(config.talkgroups, [8])

    def test_match_priority(self):
        """Test that match priority is enforced correctly"""
        logging.info("\n=== Testing Match Priority ===")
        
        # Test all combinations that should match specific ID
        radio_id = 312100  # Matches Club Network specific ID
        callsign = "WA0EDA"  # Would match WA0EDA pattern, but ID should take precedence
        logging.info(f"Testing repeater - ID: {radio_id}, Callsign: {callsign}")
        logging.info("This ID matches a Club Network rule AND the callsign matches WA0EDA pattern")
        
        config = self.matcher.get_repeater_config(radio_id, callsign)
        logging.info(f"Result: MATCHED specific ID (priority 1)")
        logging.info(f"Configuration applied: {config.description}")
        logging.info(f"Using passphrase: {config.passphrase}")
        self.assertEqual(config.passphrase, "club-network-key")  # Should match Club Network config
        
        # Test ID range vs callsign
        radio_id = 312050  # Matches KS-DMR range
        logging.info(f"\nTesting repeater - ID: {radio_id}, Callsign: {callsign}")
        logging.info("This ID matches KS-DMR range AND the callsign matches WA0EDA pattern")
        
        config = self.matcher.get_repeater_config(radio_id, callsign)
        logging.info(f"Result: MATCHED ID range (priority 2)")
        logging.info(f"Configuration applied: {config.description}")
        logging.info(f"Using passphrase: {config.passphrase}")
        self.assertEqual(config.passphrase, "ks-dmr-network-key")  # Should match KS-DMR config

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
