"""Tests for OpenBridge (OBP) support: config parsing/validation, state model,
and the wire codec. See development/openbridge-hblink3-hblink4-design.md.

Behavioral tests for _handle_openbridge_packet's routing decisions land with the
ingress-routing work (design item 4), once the handler has an observable effect
beyond logging. Here we cover the pieces that are complete: config, state, and
egress framing via the real HBProtocol._obp_build_egress.
"""
import os
import sys
from hashlib import sha1
from hmac import new as hmac_new, compare_digest

import pytest
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hblink4'))

import config as C
from models import OpenBridgeConnectionConfig, OpenBridgeState, StreamState
from hblink import HBProtocol


def _obp(name, tgslots, enabled=True, network_id=1, lp=1, tp=1):
    return {"enabled": enabled, "name": name, "network_id": network_id,
            "local_port": lp, "target_address": "x", "target_port": tp,
            "passphrase": "p", "talkgroup_slots": tgslots}


def _dmrd(dst, src=1234567, peer=3112001, slot=2, sid=b'\xde\xad\xbe\xef'):
    """Build a 53-byte DMRD frame."""
    bits = 0x80 if slot == 2 else 0x00
    return (b'DMRD' + bytes([1]) + src.to_bytes(3, 'big') + dst.to_bytes(3, 'big')
            + peer.to_bytes(4, 'big') + bytes([bits]) + sid + bytes(33))


# ---- config parsing / validation -------------------------------------------

def test_parse_ok_and_types():
    cfgs = C.parse_openbridge_connections(
        {"openbridge_connections": [_obp("A", {"31": "1", "3120": "2"})]})
    assert len(cfgs) == 1
    o = cfgs[0]
    # quoted-both map -> {3-byte TGID: int TS}
    assert o.talkgroup_slots == {(31).to_bytes(3, 'big'): 1, (3120).to_bytes(3, 'big'): 2}
    assert o.preserve_source_peer is True  # default


def test_dup_tgid_across_enabled_is_fatal():
    with pytest.raises(SystemExit):
        C.parse_openbridge_connections({"openbridge_connections": [
            _obp("A", {"31": "1"}), _obp("B", {"31": "2"})]})


def test_disabled_standby_may_mirror_tgids():
    cfgs = C.parse_openbridge_connections({"openbridge_connections": [
        _obp("A", {"31": "1"}), _obp("B", {"31": "1"}, enabled=False)]})
    assert len(cfgs) == 2


def test_bad_timeslot_is_fatal():
    with pytest.raises(SystemExit):
        C.parse_openbridge_connections({"openbridge_connections": [_obp("A", {"31": "3"})]})


def test_missing_required_field_is_fatal():
    with pytest.raises(SystemExit):
        C.parse_openbridge_connections({"openbridge_connections": [
            {"enabled": True, "name": "A", "network_id": 1, "local_port": 1,
             "target_port": 1, "passphrase": "p", "talkgroup_slots": {"31": "1"}}]})


# ---- state model ------------------------------------------------------------

def test_state_ownership_and_streams():
    o = C.parse_openbridge_connections({"openbridge_connections": [_obp("A", {"31": "1"})]})[0]
    st = OpenBridgeState(config=o, ip="1.2.3.4", port=1)
    t31 = (31).to_bytes(3, 'big')
    assert st.owns_tgid(t31) and st.ts_for_tgid(t31) == 1
    assert st.ts_for_tgid((999).to_bytes(3, 'big')) is None
    s = StreamState(repeater_id=b'\x00\x00\x00', rf_src=b'\x00\x00\x01', dst_id=t31,
                    slot=1, start_time=0, last_seen=0, stream_id=b'\xaa\xbb\xcc\xdd')
    st.add_stream(s)
    assert st.get_stream(b'\xaa\xbb\xcc\xdd') is s and len(st.streams) == 1
    st.remove_stream(b'\xaa\xbb\xcc\xdd')
    assert not st.streams


# ---- egress wire codec (real HBProtocol._obp_build_egress) ------------------

def _bare_protocol():
    # The egress builder only needs the static _obp_key, so a bare instance
    # (no __init__) is enough to exercise the real method.
    return HBProtocol.__new__(HBProtocol)


def test_egress_preserve_source_peer_and_hmac():
    o = OpenBridgeConnectionConfig(
        enabled=True, name="A", network_id=3129900, local_address="0.0.0.0",
        local_port=1, target_address="x", target_port=1, passphrase="secret",
        talkgroup_slots={(31).to_bytes(3, 'big'): 1})
    st = OpenBridgeState(config=o, ip="10.0.0.9", port=1)
    inst = _bare_protocol()
    peer = (3112001).to_bytes(4, 'big')

    pkt = inst._obp_build_egress(st, _dmrd(31, slot=2, peer=3112001), peer)
    assert len(pkt) == 73
    d = pkt[:53]
    assert compare_digest(pkt[53:], hmac_new(b"secret", d, sha1).digest())  # HMAC valid
    assert d[15] & 0x80 == 0          # wire slot forced to 1 (OBP convention)
    assert d[11:15] == peer           # preserve_source_peer keeps the true source


def test_egress_without_preserve_stamps_network_id():
    o = OpenBridgeConnectionConfig(
        enabled=True, name="A", network_id=3129900, local_address="0.0.0.0",
        local_port=1, target_address="x", target_port=1, passphrase="secret",
        talkgroup_slots={(31).to_bytes(3, 'big'): 1}, preserve_source_peer=False)
    st = OpenBridgeState(config=o, ip="10.0.0.9", port=1)
    d = _bare_protocol()._obp_build_egress(st, _dmrd(31), (3112001).to_bytes(4, 'big'))[:53]
    assert d[11:15] == (3129900).to_bytes(4, 'big')  # Brandmeister-spec: RptrId = network_id


# ---- routing: target selection + reflection guard --------------------------

def _obp_state(name, tgslots, ip="10.0.0.9", port=62035, passphrase="secret",
               network_id=3129900, preserve=True):
    cfg = OpenBridgeConnectionConfig(
        enabled=True, name=name, network_id=network_id, local_address="0.0.0.0",
        local_port=1, target_address="x", target_port=1, passphrase=passphrase,
        talkgroup_slots=tgslots, preserve_source_peer=preserve)
    return OpenBridgeState(config=cfg, ip=ip, port=port)


def test_calc_targets_obp_ownership_and_reflection():
    t31, t3120 = (31).to_bytes(3, 'big'), (3120).to_bytes(3, 'big')
    p = HBProtocol.__new__(HBProtocol)
    p._repeaters, p._outbounds = {}, {}
    p._openbridges = {"A": _obp_state("A", {t31: 1}), "B": _obp_state("B", {t3120: 2})}

    # Repeater-sourced TG31 -> only OBP A owns it
    tg = p._calculate_stream_targets(b'\x00\x00\x00\x01', 1, t31, b'\x00\x00\x00\x01', b'\x00\x00\x01')
    assert ('openbridge', 'A') in tg and ('openbridge', 'B') not in tg

    # Reflection guard: OBP A is the source of a TG31 stream -> A must NOT be a target
    tg2 = p._calculate_stream_targets(('openbridge', 'A'), 1, t31, b'\x00\x00\x00\x01', b'\x00\x00\x01')
    assert ('openbridge', 'A') not in tg2

    # Unowned TGID -> no OBP target
    tg3 = p._calculate_stream_targets(b'\x00\x00\x00\x01', 1, (999).to_bytes(3, 'big'),
                                      b'\x00\x00\x00\x01', b'\x00\x00\x01')
    assert not any(isinstance(t, tuple) and t[0] == 'openbridge' for t in tg3)


# ---- ingress lifecycle: auth -> filter -> TS-normalize -> track -> forward --

def test_ingress_creates_stream_normalizes_slot_and_forwards():
    t3120 = (3120).to_bytes(3, 'big')
    p = HBProtocol.__new__(HBProtocol)
    p._repeaters, p._outbounds = {}, {}
    st = _obp_state("A", {t3120: 2})               # TG3120 -> local TS2
    p._openbridges = {"A": st}
    p._events = Mock()                             # dashboard event emitter (stubbed)
    forwarded = []
    p._forward_stream = lambda *a, **k: forwarded.append(a)

    # Build a valid inbound OBP frame for TG3120 (wire slot 1 by convention)
    frame = p._obp_build_egress(st, _dmrd(3120, slot=1, peer=3112001), (3112001).to_bytes(4, 'big'))
    sid = frame[16:20]

    p._handle_openbridge_packet("A", frame, ("10.0.0.9", 62035))

    # Stream tracked by stream_id, on the assigned local TS2
    assert sid in st.streams and st.streams[sid].slot == 2
    # Forward invoked with source tag + assigned slot; DMR frame normalized to TS2
    assert forwarded, "forward not called"
    fdata, fsource, fslot = forwarded[0][0], forwarded[0][1], forwarded[0][2]
    assert fsource == ('openbridge', 'A') and fslot == 2
    assert fdata[15] & 0x80, "slot bit not normalized to TS2 before forwarding"

    # Dashboard stream_start event emitted, tagged as an OpenBridge source
    assert p._events.emit.called
    etype, edata = p._events.emit.call_args.args
    assert etype == 'stream_start' and edata['connection_type'] == 'openbridge' \
        and edata['connection_name'] == 'A'

    # Unmapped TGID is dropped (no stream, no forward)
    forwarded.clear()
    bad = p._obp_build_egress(st, _dmrd(999, slot=1, sid=b'\x11\x22\x33\x44'),
                              (3112001).to_bytes(4, 'big'))
    p._handle_openbridge_packet("A", bad, ("10.0.0.9", 62035))
    assert not forwarded and bad[16:20] not in st.streams
