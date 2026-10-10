"""
Tests for radio-ID takeover: a login claiming a radio ID that is already
registered from a DIFFERENT socket address.

The rule under test: a registered radio ID is never displaced by an
unauthenticated packet. A claimant from a new sockaddr is challenged and held in
_pending_logins; the incumbent is torn down only after the claimant proves the
passphrase. This matters because radio IDs are public — without the auth gate an
8-byte RPTL from any source address would evict any repeater on the network.

The invariant that must hold at every step: _repeaters is keyed by radio ID, and
only entries in _repeaters are routable, so there is never more than one
forwardable entity per radio ID.
"""

import unittest
import sys
import os
from hashlib import sha256
from time import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hblink4 import hblink as hblink_mod
from hblink4.hblink import HBProtocol
from hblink4.models import RepeaterState
from hblink4.constants import RPTACK, MSTNAK, PENDING_LOGIN_TIMEOUT
from hblink4.utils import format_uptime


PASSPHRASE = 'takeover-test-key'
RADIO_ID = b'\x00\x04\xc2\xc0'          # 312000
INCUMBENT = ('198.51.100.10', 62031)     # already registered
CLAIMANT = ('203.0.113.55', 54001)       # same site, new NAT mapping
OTHER = ('203.0.113.55', 54002)          # a second claimant racing the first, same site
REMOTE = ('198.51.100.77', 61000)        # a genuinely different host, different IP

_CONFIG = {
    'global': {'user_cache': {'timeout': 600}},
    'dashboard': {'enabled': False},
    'connection_type_detection': {'strict_hotspot_access': True},
    'repeater_configurations': {
        'patterns': [
            {
                'name': 'Test Network',
                'description': 'Fixture pattern for takeover tests',
                'match': {'id_ranges': [[312000, 312999]]},
                'config': {
                    'passphrase': PASSPHRASE,
                    'slot1_talkgroups': [8],
                    'slot2_talkgroups': [3120],
                },
            }
        ]
    },
}


class FakeTransport:
    """Records outbound datagrams instead of putting them on the wire."""

    def __init__(self):
        self.sent = []

    def sendto(self, data, addr=None):
        self.sent.append((data, addr))

    def packets_to(self, addr):
        return [data for data, dest in self.sent if dest == addr]

    def last_to(self, addr):
        pkts = self.packets_to(addr)
        return pkts[-1] if pkts else None


class TakeoverTestCase(unittest.TestCase):
    def setUp(self):
        # HBProtocol reads the module-level CONFIG at construction time.
        self._saved_config = hblink_mod.CONFIG
        hblink_mod.CONFIG = _CONFIG
        self.hb = HBProtocol()
        self.hb.transport = FakeTransport()
        self.tx = self.hb.transport

    def tearDown(self):
        hblink_mod.CONFIG = self._saved_config

    # -- helpers ------------------------------------------------------------

    def register_incumbent(self, addr=INCUMBENT):
        """Install a fully connected repeater, as if it had completed login."""
        state = RepeaterState(repeater_id=RADIO_ID, ip=addr[0], port=addr[1])
        state.connection_state = 'connected'
        state.connected = True
        state.authenticated = True
        state.callsign = b'W1AW    '
        self.hb._repeaters[RADIO_ID] = state
        return state

    def salt_from_ack(self, addr):
        """Pull the salt out of the RPTACK challenge sent to addr."""
        pkt = self.tx.last_to(addr)
        self.assertIsNotNone(pkt, f'no packet was sent to {addr}')
        self.assertTrue(pkt.startswith(RPTACK), f'expected RPTACK, got {pkt[:8]!r}')
        self.assertEqual(len(pkt), 10, 'challenge should be RPTACK + 4-byte salt')
        return int.from_bytes(pkt[6:10], 'big')

    def auth_hash(self, salt, passphrase=PASSPHRASE):
        return bytes.fromhex(
            sha256(salt.to_bytes(4, 'big') + passphrase.encode()).hexdigest()
        )

    def assert_single_routable(self):
        """The core invariant: at most one routable entity per radio ID."""
        routable = [rid for rid, r in self.hb._repeaters.items() if rid == RADIO_ID]
        self.assertLessEqual(len(routable), 1)
        # A pending claimant must never be routable, i.e. never in _repeaters.
        pending = self.hb._pending_logins.get(RADIO_ID)
        if pending is not None:
            self.assertIsNot(pending, self.hb._repeaters.get(RADIO_ID))
            self.assertIn(pending.connection_state, ('login', 'config'))
            self.assertFalse(pending.connected)
            if pending.connection_state == 'login':
                self.assertFalse(pending.authenticated)

    def admit_pending(self, addr):
        """Complete the authenticated claimant's valid WPSD RPTC admission."""
        packet = bytearray(b' ' * 302)
        packet[:16] = b'RPTC' + RADIO_ID + b'W1AW    '
        packet[222:262] = b'20260911_WPSD'.ljust(40, b' ')
        packet[262:302] = b'MMDVM_MMDVM_HS_Dual_Hat'.ljust(40, b' ')
        self.hb._handle_config(bytes(packet), addr)

    # -- the auth gate ------------------------------------------------------

    def test_login_from_new_address_does_not_evict_incumbent(self):
        """RPTL alone must never displace a live registration."""
        incumbent = self.register_incumbent()

        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)

        self.assertIs(self.hb._repeaters[RADIO_ID], incumbent,
                      'incumbent was replaced by an unauthenticated login')
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].connection_state, 'connected')
        self.assertIn(RADIO_ID, self.hb._pending_logins)
        self.assertEqual(self.hb._pending_logins[RADIO_ID].sockaddr, CLAIMANT)
        # Claimant was challenged, incumbent was not disturbed.
        self.salt_from_ack(CLAIMANT)
        self.assertEqual(self.tx.packets_to(INCUMBENT), [])
        self.assert_single_routable()

    def test_correct_passphrase_promotes_claimant_and_evicts_incumbent(self):
        self.register_incumbent()
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        salt = self.salt_from_ack(CLAIMANT)

        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(salt), CLAIMANT)

        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT)
        self.assertEqual(self.hb._pending_logins[RADIO_ID].sockaddr, CLAIMANT)
        self.assertTrue(self.hb._pending_logins[RADIO_ID].authenticated)
        self.assertEqual(self.hb._pending_logins[RADIO_ID].connection_state, 'config')
        self.assertEqual([p for p in self.tx.packets_to(INCUMBENT) if p.startswith(MSTNAK)], [])

        self.admit_pending(CLAIMANT)
        promoted = self.hb._repeaters[RADIO_ID]
        self.assertEqual(promoted.sockaddr, CLAIMANT)
        self.assertTrue(promoted.authenticated)
        self.assertEqual(promoted.connection_state, 'connected')
        self.assertNotIn(RADIO_ID, self.hb._pending_logins,
                         'claimant must not remain pending after promotion')
        # Old address is told to clean up.
        self.assertTrue(self.tx.last_to(INCUMBENT).startswith(MSTNAK))
        self.assert_single_routable()

    def test_wrong_passphrase_leaves_incumbent_untouched(self):
        incumbent = self.register_incumbent()
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        salt = self.salt_from_ack(CLAIMANT)

        self.hb._handle_auth_response(
            RADIO_ID, self.auth_hash(salt, 'wrong-key'), CLAIMANT)

        self.assertIs(self.hb._repeaters[RADIO_ID], incumbent,
                      'failed auth must not disturb the incumbent')
        self.assertEqual(self.hb._repeaters[RADIO_ID].connection_state, 'connected')
        self.assertNotIn(RADIO_ID, self.hb._pending_logins,
                         'failed claimant must be discarded')
        self.assertTrue(self.tx.last_to(CLAIMANT).startswith(MSTNAK))
        # The incumbent was never NAKed.
        self.assertEqual(
            [p for p in self.tx.packets_to(INCUMBENT) if p.startswith(MSTNAK)], [])
        self.assert_single_routable()

    def test_unanswered_challenge_expires(self):
        incumbent = self.register_incumbent()
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        pending = self.hb._pending_logins[RADIO_ID]

        # Not yet stale.
        self.hb._expire_pending_logins(pending.last_ping + 1.0)
        self.assertIn(RADIO_ID, self.hb._pending_logins)

        # Past the window.
        self.hb._expire_pending_logins(pending.last_ping + PENDING_LOGIN_TIMEOUT + 1.0)
        self.assertNotIn(RADIO_ID, self.hb._pending_logins)
        self.assertIs(self.hb._repeaters[RADIO_ID], incumbent)
        self.assert_single_routable()

    def test_stale_salt_cannot_be_replayed_after_expiry(self):
        """A challenge that aged out must not still authenticate."""
        self.register_incumbent()
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        salt = self.salt_from_ack(CLAIMANT)
        pending = self.hb._pending_logins[RADIO_ID]
        self.hb._expire_pending_logins(pending.last_ping + PENDING_LOGIN_TIMEOUT + 1.0)

        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(salt), CLAIMANT)

        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT)
        self.assert_single_routable()

    # -- races --------------------------------------------------------------

    def test_two_claimants_race_only_one_can_promote(self):
        """
        Two addresses claim the same ID before either authenticates. Only the
        holder of the challenge may promote, and only once.
        """
        self.register_incumbent()

        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        salt_a = self.salt_from_ack(CLAIMANT)
        self.hb._handle_repeater_login(RADIO_ID, OTHER)
        salt_b = self.salt_from_ack(OTHER)

        # One pending slot per radio ID; the later claim holds it.
        self.assertEqual(self.hb._pending_logins[RADIO_ID].sockaddr, OTHER)
        self.assert_single_routable()

        # The displaced claimant answers with a valid hash for its own salt and
        # must still be refused — it no longer holds the challenge.
        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(salt_a), CLAIMANT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT,
                         'a stale claimant must not evict the incumbent')
        self.assertTrue(self.tx.last_to(CLAIMANT).startswith(MSTNAK))
        self.assert_single_routable()

        # The challenge holder authenticates and takes over.
        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(salt_b), OTHER)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT)
        self.assertEqual(self.hb._pending_logins[RADIO_ID].sockaddr, OTHER)
        self.admit_pending(OTHER)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, OTHER)
        self.assert_single_routable()

        # A second, replayed RPTK from the winner must not create a duplicate.
        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(salt_b), OTHER)
        self.assertEqual(len([r for r in self.hb._repeaters if r == RADIO_ID]), 1)
        self.assert_single_routable()

    def test_claim_against_incumbent_still_in_login(self):
        """
        The incumbent has not finished registering (state='login', nothing
        de-registered yet) when a second address claims the same ID.
        """
        self.hb._handle_repeater_login(RADIO_ID, INCUMBENT)   # fresh login, no incumbent
        first_salt = self.salt_from_ack(INCUMBENT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].connection_state, 'login')

        # Second address claims the ID mid-handshake.
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT,
                         'half-registered entry must not be replaced by a bare login')
        self.assertEqual(self.hb._pending_logins[RADIO_ID].sockaddr, CLAIMANT)
        self.assert_single_routable()

        # The original finishes its handshake normally.
        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(first_salt), INCUMBENT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].connection_state, 'config')
        self.assert_single_routable()

    def test_claimant_retry_reuses_salt(self):
        """Repeated RPTL from the same claimant must not invalidate its challenge."""
        self.register_incumbent()
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        salt_first = self.salt_from_ack(CLAIMANT)
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        salt_retry = self.salt_from_ack(CLAIMANT)

        self.assertEqual(salt_first, salt_retry,
                         'retry from the same address should reuse the salt')
        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(salt_first), CLAIMANT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT)
        self.admit_pending(CLAIMANT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, CLAIMANT)
        self.assert_single_routable()

    def test_incumbent_relogin_clears_outstanding_challenge(self):
        """
        The incumbent re-logs-in from its own address while a claim is pending.
        The stale challenge must be dropped so a later RPTK is matched against
        the incumbent's new salt, not the claimant's.
        """
        self.register_incumbent()
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        stale_salt = self.salt_from_ack(CLAIMANT)

        self.hb._handle_repeater_login(RADIO_ID, INCUMBENT)
        self.assertNotIn(RADIO_ID, self.hb._pending_logins)

        # The abandoned claimant's hash is now worthless.
        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(stale_salt), CLAIMANT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT)
        self.assert_single_routable()

    def test_stale_challenge_does_not_block_a_fresh_login_from_same_address(self):
        """
        Regression: a claimant is challenged, the incumbent then times out, and the
        claimant re-logs-in from the same address. The fresh login issues a new
        salt; the abandoned challenge must not intercept the RPTK and check it
        against the old salt, which would NAK a correct answer.
        """
        self.register_incumbent()
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        old_salt = self.salt_from_ack(CLAIMANT)

        self.hb._remove_repeater(RADIO_ID, 'timeout')
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)   # fresh login path
        new_salt = self.salt_from_ack(CLAIMANT)

        self.assertNotIn(RADIO_ID, self.hb._pending_logins,
                         'fresh login must clear the moot challenge')
        self.assertNotEqual(old_salt, new_salt)

        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(new_salt), CLAIMANT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].connection_state, 'config',
                         'correct answer to the new salt was rejected')
        self.assertTrue(self.hb._repeaters[RADIO_ID].authenticated)
        self.assert_single_routable()

    def test_two_fresh_logins_no_incumbent_last_authenticated_wins(self):
        """
        Two addresses claim an unregistered ID. Arrival order decides who holds
        _repeaters, but only authentication decides who keeps it — and neither is
        routable until it reaches 'connected'.
        """
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)   # A: fresh login
        salt_a = self.salt_from_ack(CLAIMANT)
        self.hb._handle_repeater_login(RADIO_ID, OTHER)      # B: challenged
        salt_b = self.salt_from_ack(OTHER)

        # A holds the slot purely by arriving first, unauthenticated.
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, CLAIMANT)
        self.assertFalse(self.hb._repeaters[RADIO_ID].authenticated)
        self.assertEqual(self.hb._pending_logins[RADIO_ID].sockaddr, OTHER)
        # Neither is routable yet.
        self.assertNotEqual(self.hb._repeaters[RADIO_ID].connection_state, 'connected')
        self.assert_single_routable()

        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(salt_a), CLAIMANT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, CLAIMANT)
        self.assert_single_routable()

        # B proves the passphrase too and takes it.
        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(salt_b), OTHER)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, CLAIMANT)
        self.assertEqual(self.hb._pending_logins[RADIO_ID].sockaddr, OTHER)
        self.admit_pending(OTHER)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, OTHER)
        self.assert_single_routable()

    def test_takeover_after_incumbent_already_gone(self):
        """A pending claim must still complete if the incumbent times out first."""
        self.register_incumbent()
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        salt = self.salt_from_ack(CLAIMANT)

        self.hb._remove_repeater(RADIO_ID, 'timeout')
        self.assertNotIn(RADIO_ID, self.hb._repeaters)

        self.hb._handle_auth_response(RADIO_ID, self.auth_hash(salt), CLAIMANT)
        self.assertNotIn(RADIO_ID, self.hb._repeaters)
        self.assertEqual(self.hb._pending_logins[RADIO_ID].connection_state, 'config')
        self.admit_pending(CLAIMANT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, CLAIMANT)
        self.assertEqual(self.hb._repeaters[RADIO_ID].connection_state, 'connected')
        self.assert_single_routable()

    def test_pending_claimant_is_never_a_forwarding_target(self):
        """
        The whole point of the pending map: a claimant must not appear anywhere
        the router can see it while the incumbent is still live.
        """
        incumbent = self.register_incumbent()
        incumbent.slot2_talkgroups = {b'\x00\x0c\x30'}   # make it a real eligible target
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)

        targets = self.hb._calculate_stream_targets(
            b'\x00\x04\xc2\xc1',           # some other source repeater
            2, b'\x00\x0c\x30', b'\x11\x22\x33\x44', b'\x2f\xa9\x05')

        # Sanity: the incumbent IS routable, so this test is not vacuous.
        self.assertIn(RADIO_ID, targets,
                      'incumbent should be an eligible target; test setup is wrong')
        # Targets are radio IDs; every one must resolve to a live registration,
        # and the ID in question must resolve to the incumbent, never the claimant.
        for t in targets:
            if isinstance(t, bytes):
                resolved = self.hb._repeaters[t]
                self.assertNotEqual(resolved.sockaddr, CLAIMANT,
                                    'a pending claimant became a forwarding target')
        self.assertEqual(self.hb._repeaters[RADIO_ID].sockaddr, INCUMBENT)
        self.assert_single_routable()


class ReconnectReportingTestCase(unittest.TestCase):
    """
    A login for a radio ID that is already registered reports how long the
    previous session lasted. We report the fact; the operator judges it.
    """

    def setUp(self):
        self._saved_config = hblink_mod.CONFIG
        hblink_mod.CONFIG = _CONFIG
        self.hb = HBProtocol()
        self.hb.transport = FakeTransport()

    def tearDown(self):
        hblink_mod.CONFIG = self._saved_config

    def install(self, addr, connected_ago):
        state = RepeaterState(repeater_id=RADIO_ID, ip=addr[0], port=addr[1])
        state.connection_state = 'connected'
        state.connected = True
        state.authenticated = True
        state.connect_time = time() - connected_ago
        self.hb._repeaters[RADIO_ID] = state
        return state

    def reconnect_lines(self, logs):
        return [m for m in logs.output if 'since last connect' in m]

    def test_first_login_reports_no_reconnect(self):
        with self.assertLogs('hblink4.hblink', level='INFO') as logs:
            self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        self.assertEqual(self.reconnect_lines(logs), [],
                         'a first-ever login is not a reconnect')

    def test_reconnect_from_new_address_reports_both_addresses(self):
        self.install(INCUMBENT, connected_ago=5)
        with self.assertLogs('hblink4.hblink', level='INFO') as logs:
            self.hb._handle_repeater_login(RADIO_ID, REMOTE)

        lines = self.reconnect_lines(logs)
        self.assertEqual(len(lines), 1)
        self.assertIn(f'{REMOTE[0]}:{REMOTE[1]}', lines[0])       # where it is now
        self.assertIn(f'{INCUMBENT[0]}:{INCUMBENT[1]}', lines[0])  # where it was
        self.assertIn('0:00:00:05', lines[0])

    def test_login_stage_retry_is_not_called_a_reconnect(self):
        """
        A repeater retrying RPTL because it never got the ACK has not connected,
        so there is no session to report. connect_time in a pre-'connected' state
        is only the object's creation time — reporting it would state something
        untrue. The 'login retry, resending same salt' line covers this case.
        """
        with self.assertLogs('hblink4.hblink', level='INFO') as logs:
            self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)   # first attempt
            self.hb._repeaters[RADIO_ID].connect_time -= 5       # 5s passes
            self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)   # retry

        self.assertEqual(self.hb._repeaters[RADIO_ID].connection_state, 'login')
        self.assertEqual(self.reconnect_lines(logs), [],
                         'a device that never connected must not be called a reconnect')
        self.assertTrue([m for m in logs.output if 'login retry' in m],
                        'the retry itself should still be logged')

    def test_config_stage_retry_is_not_called_a_reconnect(self):
        """Authenticated but never configured is still not a completed session."""
        self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        state = self.hb._repeaters[RADIO_ID]
        state.connection_state = 'config'
        state.authenticated = True
        state.connect_time -= 30

        with self.assertLogs('hblink4.hblink', level='INFO') as logs:
            self.hb._handle_repeater_login(RADIO_ID, CLAIMANT)
        self.assertEqual(self.reconnect_lines(logs), [])

    def test_reconnect_from_same_address_is_still_reported(self):
        """The 'cannot stay logged in' case — same address, over and over."""
        self.install(INCUMBENT, connected_ago=5)
        with self.assertLogs('hblink4.hblink', level='INFO') as logs:
            self.hb._handle_repeater_login(RADIO_ID, INCUMBENT)

        lines = self.reconnect_lines(logs)
        self.assertEqual(len(lines), 1)
        self.assertIn('0:00:00:05', lines[0])

    def test_long_session_renders_days_when_reporting_is_unfiltered(self):
        """
        With the threshold disabled, a multi-day session renders in full. The
        default threshold suppresses this case as routine — see
        ReconnectThresholdTestCase — so reporting must be turned off to see it.
        """
        hblink_mod.CONFIG = {**_CONFIG,
                             'global': {**_CONFIG['global'],
                                        'reconnect_report_threshold': 0}}
        self.install(INCUMBENT, connected_ago=(3 * 86400) + (4 * 3600) + (17 * 60) + 9)
        with self.assertLogs('hblink4.hblink', level='INFO') as logs:
            self.hb._handle_repeater_login(RADIO_ID, REMOTE)
        self.assertIn('3:04:17:09', self.reconnect_lines(logs)[0])

    def test_connect_time_is_stamped_on_reaching_connected(self):
        """Without the re-stamp the delta would measure from object creation."""
        state = RepeaterState(repeater_id=RADIO_ID, ip=CLAIMANT[0], port=CLAIMANT[1])
        state.connect_time = time() - 9999
        state.authenticated = True
        state.connection_state = 'config'
        self.hb._repeaters[RADIO_ID] = state

        config_packet = bytearray(b' ' * 302)
        config_packet[:16] = b'RPTC' + RADIO_ID + b'W1AW    '
        config_packet[222:262] = b'20260911_WPSD'.ljust(40, b' ')
        config_packet[262:302] = b'MMDVM_MMDVM_HS_Dual_Hat'.ljust(40, b' ')
        self.hb._handle_config(bytes(config_packet), CLAIMANT)

        self.assertEqual(state.connection_state, 'connected')
        self.assertLess(time() - state.connect_time, 5,
                        'connect_time should be re-stamped when the session starts')


class ReconnectThresholdTestCase(unittest.TestCase):
    """
    reconnect_report_threshold filters out routine reconnects: only sessions
    SHORTER than the threshold are reported, because those are the ones that
    indicate a device which cannot stay online.
    """

    def setUp(self):
        self._saved_config = hblink_mod.CONFIG

    def tearDown(self):
        hblink_mod.CONFIG = self._saved_config

    def build(self, threshold='unset'):
        config = dict(_CONFIG)
        config['global'] = dict(_CONFIG['global'])
        if threshold != 'unset':
            config['global']['reconnect_report_threshold'] = threshold
        hblink_mod.CONFIG = config
        hb = HBProtocol()
        hb.transport = FakeTransport()
        return hb

    def attempt(self, hb, session_length):
        state = RepeaterState(repeater_id=RADIO_ID, ip=INCUMBENT[0], port=INCUMBENT[1])
        state.connection_state = 'connected'
        state.connected = True
        state.connect_time = time() - session_length
        hb._repeaters[RADIO_ID] = state
        with self.assertLogs('hblink4.hblink', level='INFO') as logs:
            hb._handle_repeater_login(RADIO_ID, REMOTE)
        return [m for m in logs.output if 'since last connect' in m]

    def test_short_session_is_reported(self):
        hb = self.build(threshold=3600)
        self.assertEqual(len(self.attempt(hb, session_length=5)), 1)

    def test_long_session_is_suppressed(self):
        hb = self.build(threshold=3600)
        self.assertEqual(self.attempt(hb, session_length=3 * 86400), [],
                         'a routine reconnect after days should not be reported')

    def test_boundary_is_exclusive(self):
        """Exactly at the threshold counts as routine."""
        hb = self.build(threshold=3600)
        self.assertEqual(self.attempt(hb, session_length=3601), [])
        hb = self.build(threshold=3600)
        self.assertEqual(len(self.attempt(hb, session_length=3599)), 1)

    def test_zero_reports_everything(self):
        hb = self.build(threshold=0)
        self.assertEqual(len(self.attempt(hb, session_length=30 * 86400)), 1)

    def test_missing_config_key_uses_default(self):
        """An existing config with no such key must keep working."""
        hb = self.build()  # key absent entirely
        self.assertEqual(len(self.attempt(hb, session_length=5)), 1)
        hb = self.build()
        self.assertEqual(self.attempt(hb, session_length=7200), [],
                         'default threshold of 3600 should suppress a 2h session')

    def test_takeover_still_proceeds_when_report_is_suppressed(self):
        """Filtering the log line must not change any protocol behavior."""
        hb = self.build(threshold=3600)
        self.attempt(hb, session_length=3 * 86400)
        self.assertIn(RADIO_ID, hb._pending_logins,
                      'the claimant should still have been challenged')
        self.assertEqual(hb._repeaters[RADIO_ID].sockaddr, INCUMBENT)


class ConnectTimeEventTestCase(unittest.TestCase):
    """
    The dashboard derives uptime from this value. It must come from the server,
    because repeater_connected is emitted on missed pings and on state replay,
    where the event's arrival time would wrongly reset uptime.
    """

    def setUp(self):
        self._saved_config = hblink_mod.CONFIG
        hblink_mod.CONFIG = _CONFIG
        self.hb = HBProtocol()
        self.hb.transport = FakeTransport()

    def tearDown(self):
        hblink_mod.CONFIG = self._saved_config

    def test_connect_time_is_included_in_event_payload(self):
        state = RepeaterState(repeater_id=RADIO_ID, ip=INCUMBENT[0], port=INCUMBENT[1])
        state.connect_time = time() - 12345
        payload = self.hb._prepare_repeater_event_data(RADIO_ID, state)
        self.assertIn('connect_time', payload)
        self.assertEqual(payload['connect_time'], state.connect_time)

    def test_connect_time_survives_a_missed_ping_and_recovery(self):
        """The bug this fixes: uptime resetting for a repeater that never dropped."""
        state = RepeaterState(repeater_id=RADIO_ID, ip=INCUMBENT[0], port=INCUMBENT[1])
        state.connection_state = 'connected'
        state.connected = True
        original = time() - 86400
        state.connect_time = original
        self.hb._repeaters[RADIO_ID] = state

        state.missed_pings = 2                       # missed pings, then recovery
        self.hb._handle_ping(RADIO_ID, INCUMBENT)

        self.assertEqual(state.connect_time, original,
                         'recovery must not restart the session clock')
        payload = self.hb._prepare_repeater_event_data(RADIO_ID, state)
        self.assertEqual(payload['connect_time'], original)


class FormatUptimeTestCase(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(format_uptime(0), '0:00:00:00')
        self.assertEqual(format_uptime(5), '0:00:00:05')
        self.assertEqual(format_uptime(65), '0:00:01:05')
        self.assertEqual(format_uptime(3671), '0:01:01:11')
        self.assertEqual(format_uptime(86400), '1:00:00:00')
        self.assertEqual(format_uptime(273429), '3:03:57:09')

    def test_fractional_seconds_truncate(self):
        self.assertEqual(format_uptime(5.9), '0:00:00:05')

    def test_negative_clamps_to_zero(self):
        """Clock adjustments must not produce nonsense in the log."""
        self.assertEqual(format_uptime(-10), '0:00:00:00')


if __name__ == '__main__':
    unittest.main()
