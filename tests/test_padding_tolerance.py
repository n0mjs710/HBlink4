"""
Config-field padding, both directions.

Receive: a client may pad the fixed-width RPTC blob with NUL rather than the
space DMRGateway emits. str.strip() takes whitespace and not NUL, so unstripped
padding reaches dashboard JSON, float() conversions and callsign ACL matching
(where a pattern anchored as ^W0UK$ stops matching) with the fill attached.

Send: the blob this server emits, per DMRGateway's three padding conventions.
"""

import unittest

from hblink4.access_control import RepeaterMatcher
from hblink4.utils import pad_decimal, pad_num, pad_text, safe_decode_bytes


# Exact callsign pattern (no wildcard): re.escape + ^...$ anchoring is the case
# trailing padding breaks. A "W0UK*" pattern absorbs the fill into its .*.
_CONFIG = {
    "repeaters": {
        "patterns": [
            {
                "name": "Bridge endpoints",
                "match": {"callsigns": ["W0UK"]},
                "config": {
                    "passphrase": "bridge-key",
                    "slot1_talkgroups": [2, 9],
                    "slot2_talkgroups": [3120],
                },
            },
        ],
        "default": {
            "passphrase": "passw0rd",
            "slot1_talkgroups": [1],
            "slot2_talkgroups": [2],
        },
    }
}


class TestConfigFieldPadding(unittest.TestCase):
    def test_nul_padding_is_stripped(self):
        self.assertEqual(safe_decode_bytes(b'W0UK' + b'\x00' * 4), 'W0UK')
        self.assertEqual(
            safe_decode_bytes(b'Lawrence, KS' + b'\x00' * 8), 'Lawrence, KS')

    def test_space_padding_still_stripped(self):
        self.assertEqual(safe_decode_bytes(b'WA0EDA  '), 'WA0EDA')
        self.assertEqual(safe_decode_bytes(b'Lawrence, KS        '), 'Lawrence, KS')

    def test_padded_numeric_field_parses(self):
        self.assertEqual(float(safe_decode_bytes(b'38.8500' + b'\x00')), 38.85)

    def test_empty_and_all_padding(self):
        self.assertEqual(safe_decode_bytes(b''), '')
        self.assertEqual(safe_decode_bytes(b'\x00' * 124), '')
        self.assertEqual(safe_decode_bytes(b' ' * 124), '')


class TestOutboundFieldEncoders(unittest.TestCase):
    # Each mirrors one conversion in DMRGateway's config-blob sprintf,
    # "%-8.8s%09u%09u%02u%02u%8.8s%9.9s%03d%-20.20s%-19.19s%c%-124.124s%-40.40s%-40.40s".
    def test_text_is_left_justified_and_space_filled(self):
        self.assertEqual(pad_text('W0UK', 8), b'W0UK    ')
        self.assertEqual(pad_text('Lawrence, KS', 20), b'Lawrence, KS' + b' ' * 8)

    def test_numeric_is_right_justified_and_zero_filled(self):
        self.assertEqual(pad_num(5, 2), b'05')          # "%02u" -- not b'5 '
        self.assertEqual(pad_num('1', 2), b'01')
        self.assertEqual(pad_num(10, 3), b'010')        # "%03d"
        self.assertEqual(pad_num(444000000, 9), b'444000000')

    def test_decimal_is_right_justified_and_space_filled(self):
        self.assertEqual(pad_decimal('38.8500', 8), b' 38.8500')
        self.assertEqual(pad_decimal('-097.6114', 9), b'-097.6114')

    def test_no_encoder_emits_nul(self):
        for field in (pad_text('', 124), pad_num('', 9), pad_decimal('', 8)):
            self.assertNotIn(0, field)

    def test_overlong_values_are_truncated_to_the_field_width(self):
        self.assertEqual(pad_text('WAYTOOLONGCALL', 8), b'WAYTOOLO')
        self.assertEqual(len(pad_num('1234567890123', 9)), 9)


class TestCallsignACLWithPaddedCallsign(unittest.TestCase):
    def setUp(self):
        self.matcher = RepeaterMatcher(_CONFIG)

    def test_nul_padded_callsign_matches_exact_pattern(self):
        callsign = safe_decode_bytes(b'W0UK' + b'\x00' * 4)
        config = self.matcher.get_repeater_config(3120666, callsign)
        self.assertEqual(config.passphrase, 'bridge-key')
        self.assertEqual(config.slot2_talkgroups, [3120])

    def test_space_padded_callsign_matches_exact_pattern(self):
        callsign = safe_decode_bytes(b'W0UK    ')
        config = self.matcher.get_repeater_config(3120666, callsign)
        self.assertEqual(config.passphrase, 'bridge-key')

    def test_unpadded_callsign_still_falls_through_to_default(self):
        config = self.matcher.get_repeater_config(3120666, 'KB1ABC')
        self.assertEqual(config.passphrase, 'passw0rd')


if __name__ == '__main__':
    unittest.main()
