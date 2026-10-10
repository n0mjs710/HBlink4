"""Regression tests for the HomeBrew RPTC wire framing and session checks."""

import pytest

from hblink4 import hblink as hblink_mod
from hblink4.constants import MSTNAK
from hblink4.hblink import HBProtocol
from hblink4.models import RepeaterState


RADIO_ID = b"\x00\x04\xc2\xc0"
OWNER = ("198.51.100.10", 62031)
OTHER_ADDRESS = ("203.0.113.55", 54001)
PASSPHRASE = "rptc-framing-test-key"

CONFIG = {
    "global": {"user_cache": {"timeout": 600}},
    "dashboard": {"enabled": False},
    "connection_type_detection": {"strict_hotspot_access": True},
    "repeater_configurations": {
        "patterns": [{
            "name": "RPTC test network",
            "match": {"id_ranges": [[312000, 312999]]},
            "config": {
                "passphrase": PASSPHRASE,
                "trust": False,
                "slot1_talkgroups": [],
                "slot2_talkgroups": [100],
            },
        }]
    },
}


class FakeTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr=None):
        self.sent.append((data, addr))


@pytest.fixture
def hb_protocol(monkeypatch):
    monkeypatch.setattr(hblink_mod, "CONFIG", CONFIG)
    protocol = HBProtocol()
    protocol.transport = FakeTransport()
    return protocol


def make_rptc(length=302, *, software_id=b"20260911_WPSD",
              package_id=b"MMDVM_MMDVM_HS_Dual_Hat"):
    """Return the fixed RPTC fields padded to the requested wire length."""
    packet = bytearray(b" " * length)
    packet[:16] = b"RPTC" + RADIO_ID + b"W1AW    "
    software_width = max(0, min(40, length - 222))
    packet[222:222 + software_width] = software_id[:software_width].ljust(software_width, b" ")
    if length > 262:
        package_width = length - 262
        packet[262:] = package_id[:package_width].ljust(package_width, b" ")
    return bytes(packet)


def install_config_session(protocol, address=OWNER, *, authenticated=True,
                           connection_state="config", connected=False):
    state = RepeaterState(repeater_id=RADIO_ID, ip=address[0], port=address[1])
    state.authenticated = authenticated
    state.connection_state = connection_state
    state.connected = connected
    protocol._repeaters[RADIO_ID] = state
    return state


@pytest.mark.parametrize("length", [301, 303])
def test_authenticated_rptc_with_wrong_length_is_nak_and_removed(hb_protocol, length):
    state = install_config_session(hb_protocol)

    hb_protocol._handle_config(make_rptc(length), OWNER)

    assert RADIO_ID not in hb_protocol._repeaters
    assert state.connection_state == "config"
    assert hb_protocol.transport.sent[-1] == (MSTNAK + RADIO_ID, OWNER)


def test_authenticated_rptc_of_exactly_302_bytes_connects(hb_protocol):
    state = install_config_session(hb_protocol)
    packet = make_rptc(302)

    hb_protocol._handle_config(packet, OWNER)

    assert len(packet) == 302
    assert hb_protocol._repeaters[RADIO_ID] is state
    assert state.connection_state == "connected"
    assert state.connected is True
    assert state.callsign == b"W1AW    "
    assert state.package_id == packet[262:302]
    assert state.connection_type == "hotspot"
    assert hb_protocol.transport.sent[-1] == (b"RPTACK" + RADIO_ID, OWNER)


def test_malformed_rptc_from_non_owner_preserves_incumbent(hb_protocol):
    incumbent = install_config_session(
        hb_protocol, authenticated=True, connection_state="connected", connected=True
    )
    incumbent.callsign = b"W1AW    "

    hb_protocol._handle_config(make_rptc(301), OTHER_ADDRESS)

    assert hb_protocol._repeaters[RADIO_ID] is incumbent
    assert incumbent.connection_state == "connected"
    assert incumbent.connected is True
    assert incumbent.callsign == b"W1AW    "
    assert hb_protocol.transport.sent[-1] == (MSTNAK + RADIO_ID, OTHER_ADDRESS)


def test_unauthenticated_rptc_cannot_connect_or_remove_session(hb_protocol):
    state = install_config_session(
        hb_protocol, authenticated=False, connection_state="login", connected=False
    )

    hb_protocol._handle_config(make_rptc(302), OWNER)

    assert hb_protocol._repeaters[RADIO_ID] is state
    assert state.connection_state == "login"
    assert state.connected is False
    assert state.callsign == b""
    assert hb_protocol.transport.sent[-1] == (MSTNAK + RADIO_ID, OWNER)


def test_authenticated_rptc_in_wrong_state_cannot_connect_or_remove_session(hb_protocol):
    state = install_config_session(
        hb_protocol, authenticated=True, connection_state="login", connected=False
    )

    hb_protocol._handle_config(make_rptc(302), OWNER)

    assert hb_protocol._repeaters[RADIO_ID] is state
    assert state.connection_state == "login"
    assert state.connected is False
    assert state.callsign == b""
    assert hb_protocol.transport.sent[-1] == (MSTNAK + RADIO_ID, OWNER)


@pytest.mark.parametrize(
    ("software_id", "package_id", "expected"),
    [
        (b"20260911_WPSD", b"MMDVM_HBlink", "network"),
        (b"", b"", "unknown"),
    ],
)
def test_strict_hotspot_mode_rejects_non_hotspot_and_unknown_metadata(
    hb_protocol, software_id, package_id, expected
):
    state = install_config_session(hb_protocol)
    emitted = []
    hb_protocol._events.emit = lambda event_type, data: emitted.append((event_type, data))

    hb_protocol._handle_config(
        make_rptc(software_id=software_id, package_id=package_id), OWNER
    )

    assert RADIO_ID not in hb_protocol._repeaters
    assert state.connection_state == "config" and not state.connected
    assert hb_protocol.transport.sent[-1] == (MSTNAK + RADIO_ID, OWNER)
    event_type, audit = next(
        (event_type, data) for event_type, data in emitted
        if event_type == "client_admission_rejected"
    )
    assert event_type == "client_admission_rejected"
    assert audit == {
        "repeater_id": int.from_bytes(RADIO_ID, "big"),
        "callsign": "W1AW",
        "classification": expected,
        "reason": "profile_not_allowed",
    }
    assert "address" not in audit and "passphrase" not in audit


def test_strict_disabled_preserves_allowlist_auth_and_accepts_other_class(hb_protocol):
    hb_protocol._config = {"connection_type_detection": {"strict_hotspot_access": False}}
    state = install_config_session(hb_protocol)

    hb_protocol._handle_config(make_rptc(package_id=b"MMDVM_HBlink"), OWNER)

    assert hb_protocol._repeaters[RADIO_ID] is state
    assert state.connection_state == "connected" and state.connected
    assert state.connection_type == "network"


def test_dmrd_before_config_admission_never_creates_voice_stream(hb_protocol):
    state = install_config_session(hb_protocol, connection_state="config")
    packet = bytearray(55)
    packet[:4] = b"DMRD"
    packet[11:15] = RADIO_ID

    hb_protocol._handle_dmr_data(bytes(packet), OWNER)

    assert state.connection_state == "config"
    assert state.get_slot_stream(1) is None and state.get_slot_stream(2) is None
