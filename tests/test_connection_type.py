"""
Tests for connection type detection based on package_id and software_id
"""
import unittest
import sys
import os

# Add the hblink4 module to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hblink4.utils import detect_connection_type


class TestConnectionTypeDetection(unittest.TestCase):
    """Test detect_connection_type function"""
    
    # ========== Package ID based detection (primary) ==========
    
    def test_mmdvm_hs_hat_is_hotspot(self):
        """MMDVM_HS_Hat package should be detected as hotspot"""
        self.assertEqual(detect_connection_type(b'', b'MMDVM_MMDVM_HS_Hat'), 'hotspot')
        self.assertEqual(detect_connection_type(b'', b'MMDVM_MMDVM_HS_Dual_Hat'), 'hotspot')
        
    def test_dmo_is_hotspot(self):
        """Direct Mode Operation (DMO) should be detected as hotspot"""
        self.assertEqual(detect_connection_type(b'', b'MMDVM_DMO'), 'hotspot')
        
    def test_dvmega_package_is_hotspot(self):
        """DVMEGA package should be detected as hotspot"""
        self.assertEqual(detect_connection_type(b'', b'MMDVM_DVMEGA'), 'hotspot')
        
    def test_zumspot_package_is_hotspot(self):
        """ZUMspot package should be detected as hotspot"""
        self.assertEqual(detect_connection_type(b'', b'MMDVM_ZUMspot'), 'hotspot')
        
    def test_hblink_package_is_network(self):
        """HBlink package should be detected as network"""
        self.assertEqual(detect_connection_type(b'', b'MMDVM_HBlink'), 'network')
        self.assertEqual(detect_connection_type(b'', b'HBlink4'), 'network')
        
    def test_generic_mmdvm_is_repeater(self):
        """Generic MMDVM (without qualifiers) should be repeater"""
        self.assertEqual(detect_connection_type(b'', b'MMDVM'), 'repeater')
        
    def test_mmdvm_unknown_is_repeater(self):
        """MMDVM_Unknown should be repeater (default config includes 'unknown')"""
        self.assertEqual(detect_connection_type(b'', b'MMDVM_Unknown'), 'repeater')
        
    # ========== Software ID fallback detection ==========
    
    def test_pistar_software_is_hotspot(self):
        """Pi-Star software should be detected as hotspot (fallback)"""
        self.assertEqual(detect_connection_type(b'20181107_Pi-Star', b''), 'hotspot')
        self.assertEqual(detect_connection_type(b'20240210_PS4', b''), 'hotspot')
        
    def test_wpsd_software_is_hotspot(self):
        """WPSD software should be detected as hotspot (fallback)"""
        self.assertEqual(detect_connection_type(b'20251120_WPSD', b''), 'hotspot')
        
    def test_hblink_software_is_network(self):
        """HBlink software should be detected as network (fallback)"""
        self.assertEqual(detect_connection_type(b'HBlink3', b''), 'network')
        self.assertEqual(detect_connection_type(b'HBlink4', b''), 'network')
        
    def test_freedmr_software_is_network(self):
        """FreeDMR software should be detected as network (fallback)"""
        self.assertEqual(detect_connection_type(b'FreeDMR', b''), 'network')
        
    # ========== Combined detection (package takes precedence) ==========
    
    def test_package_takes_precedence(self):
        """Package ID should take precedence over software ID"""
        self.assertEqual(detect_connection_type(b'HBlink4', b'MMDVM_MMDVM_HS_Hat'), 'hotspot')
        self.assertEqual(detect_connection_type(b'20240210_PS4', b'MMDVM_HBlink'), 'network')
        
    # ========== Real-world examples from actual connections ==========
    
    def test_real_world_n0eye_hotspot(self):
        """N0EYE: Pi-Star 4 with dual hat = hotspot"""
        self.assertEqual(
            detect_connection_type(b'20240210_PS4', b'MMDVM_MMDVM_HS_Dual_Hat'),
            'hotspot'
        )
        
    def test_real_world_wpsd_dmo(self):
        """KF0RIZ: WPSD with DMO = hotspot"""
        self.assertEqual(
            detect_connection_type(b'20251120_WPSD', b'MMDVM_DMO'),
            'hotspot'
        )
        
    def test_real_world_wpsd_mmdvm(self):
        """N7KLR: WPSD with generic MMDVM = repeater"""
        self.assertEqual(
            detect_connection_type(b'20251120_WPSD', b'MMDVM'),
            'repeater'
        )
        
    def test_real_world_hblink_network(self):
        """K0BOY: HBlink network connection"""
        self.assertEqual(
            detect_connection_type(b'20170620', b'MMDVM_HBlink'),
            'network'
        )
        
    # ========== Edge cases ==========
    
    def test_empty_both(self):
        """Empty software_id and package_id should return unknown"""
        self.assertEqual(detect_connection_type(b'', b''), 'unknown')
        self.assertEqual(detect_connection_type(b'', None), 'unknown')
        self.assertEqual(detect_connection_type(None, None), 'unknown')
        
    def test_null_padded_strings(self):
        """Should handle null-padded strings (as sent in RPTC packets)"""
        package_id = b'MMDVM_MMDVM_HS_Hat' + b'\x00' * 22
        self.assertEqual(detect_connection_type(b'', package_id), 'hotspot')
        
    def test_case_insensitive(self):
        """Detection should be case insensitive"""
        self.assertEqual(detect_connection_type(b'', b'MMDVM_HS'), 'hotspot')
        self.assertEqual(detect_connection_type(b'', b'mmdvm_hs'), 'hotspot')
        self.assertEqual(detect_connection_type(b'HBLINK4', b''), 'network')
    
    # ========== Custom config tests ==========
    
    def test_custom_config_hotspot_packages(self):
        """Custom config should override default hotspot packages"""
        config = {
            'connection_type_detection': {
                'hotspot_packages': ['custom_hotspot'],
                'network_packages': [],
                'repeater_packages': []
            }
        }
        self.assertEqual(detect_connection_type(b'', b'Custom_Hotspot_Device', config), 'hotspot')
        # Default patterns should not match when overridden
        self.assertEqual(detect_connection_type(b'', b'MMDVM_HS_Hat', config), 'unknown')
        
    def test_custom_config_network_packages(self):
        """Custom config should override default network packages"""
        config = {
            'connection_type_detection': {
                'hotspot_packages': [],
                'network_packages': ['myserver'],
                'repeater_packages': []
            }
        }
        self.assertEqual(detect_connection_type(b'', b'MyServer_v1.0', config), 'network')


if __name__ == '__main__':
    unittest.main()
