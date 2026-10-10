"""Public snapshot API and WebSocket exposure tests."""

import atexit
import json
from collections import deque
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dashboard import server


PRIVATE_SENTINELS = (
    "198.51.100.44",
    "02:00:00:00:00:01",
    "PRIVATE_CONFIG_SENTINEL",
    "PRIVATE_STREAM_SENTINEL",
    "PRIVATE_AUTH_SENTINEL",
)


class FakeUserDb:
    def get(self, radio_id, default=""):
        return {724287099: "py2abc"}.get(radio_id, default)


class FakeAdminStore:
    def list_operators(self):
        return [
            {"callsign": "PY2ABC", "active": True,
             "password_hash": "PRIVATE_AUTH_SENTINEL", "base_id": 7242870,
             "essid_from": 0, "essid_to": 99},
            {"callsign": "PY2OFF", "active": False,
             "password_hash": "PRIVATE_AUTH_SENTINEL", "base_id": 7242880,
             "essid_from": 0, "essid_to": 99},
        ]


@pytest.fixture
def public_client(monkeypatch):
    state = SimpleNamespace(
        repeaters={},
        streams={},
        events=deque(maxlen=500),
        user_db=FakeUserDb(),
        hblink_connected=False,
        public_websocket_clients=set(),
        websocket_clients=set(),
    )
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server.app.state, "admin_store", FakeAdminStore(), raising=False)
    atexit.unregister(server.save_persistent_data)
    with TestClient(server.public_app) as client:
        yield client, state


def connected_hotspot(callsign, *, tg100_active=False, ip="198.51.100.44"):
    return {
        "callsign": callsign,
        "status": "connected",
        "connection_type": "hotspot",
        "dynamic_talkgroup_active": tg100_active,
        "ip": ip,
        "remote_port": 62031,
        "mac": "02:00:00:00:00:01",
        "configuration": "PRIVATE_CONFIG_SENTINEL",
    }


def active_stream(repeater_id, *, call_type="group", is_data=False, is_assumed=False,
                  slot=2, dst_id=100, status="active"):
    return {
        "repeater_id": repeater_id,
        "status": status,
        "slot": slot,
        "dst_id": dst_id,
        "call_type": call_type,
        "is_data": is_data,
        "is_assumed": is_assumed,
        "src_id": 724287099,
        "stream_id": "PRIVATE_STREAM_SENTINEL",
        "rf_src": 724287099,
        "start_time": datetime.now().timestamp() - 5,
        "private": "PRIVATE_AUTH_SENTINEL",
    }


def public_activity_event(*, call_type="group", slot=2, dst_id=100,
                          is_data=False, is_assumed=False, timestamp=None):
    return {
        "type": "stream_end",
        "timestamp": datetime.now().timestamp() if timestamp is None else timestamp,
        "data": {
            "src_id": 724287099,
            "call_type": call_type,
            "slot": slot,
            "dst_id": dst_id,
            "is_data": is_data,
            "is_assumed": is_assumed,
            "stream_id": "PRIVATE_STREAM_SENTINEL",
            "duration": 4.2,
            "repeater_id": 724287002,
            "ip": "198.51.100.44",
        },
    }


def assert_sanitized_snapshot(snapshot):
    assert set(snapshot) == {"state", "metrics", "hotspots", "active_transmissions", "activity", "operators"}
    assert set(snapshot["state"]) == {"dashboard", "radio"}
    assert set(snapshot["metrics"]) == {
        "hotspots_connected", "tg100_active_hotspots", "tg100_transmitting",
        "operators_authorized",
    }
    assert all(set(item) == {"callsign", "connected", "tg100_active", "transmitting"}
               for item in snapshot["hotspots"])
    for item in snapshot["active_transmissions"]:
        assert set(item) == {"speaker", "ingress", "talkgroup", "timeslot", "started_at"}
        assert set(item["speaker"]) == {"dmr_id", "callsign"}
        assert set(item["ingress"]) <= {"repeater_id", "callsign", "essid"}
    for item in snapshot["activity"]:
        assert set(item) == {"speaker", "ingress", "talkgroup", "timeslot",
                             "duration_seconds", "started_at", "ended_at"}
        assert set(item["speaker"]) == {"dmr_id", "callsign"}
        assert set(item["ingress"]) <= {"repeater_id", "callsign", "essid"}
    assert all(set(item) == {"callsign"} for item in snapshot["operators"])
    encoded = json.dumps(snapshot)
    assert all(secret not in encoded for secret in PRIVATE_SENTINELS)
    forbidden_keys = {
        "radio_id", "base_id", "src_id", "rf_src", "stream_id",
        "ip", "remote_port", "mac", "config", "configuration",
        "password", "password_hash", "passphrase", "cookie", "csrf", "session",
    }

    def keys(value):
        if isinstance(value, dict):
            yield from value.keys()
            for nested in value.values():
                yield from keys(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from keys(nested)

    assert not (forbidden_keys & set(keys(snapshot)))


def test_snapshot_api_is_allowlisted_and_separates_tg_activity_from_transmission(public_client):
    client, state = public_client
    state.hblink_connected = True
    state.repeaters = {
        1: connected_hotspot("py2aaa", tg100_active=True),
        2: connected_hotspot("PY2ABC", tg100_active=False),
        3: {**connected_hotspot("PY2REP"), "connection_type": "repeater"},
        4: {**connected_hotspot("PY2OFF"), "status": "disconnected"},
    }
    state.streams = {
        "hotspot-a.2": active_stream(1, status="hang_time"),
        "hotspot-b.2": active_stream(2),
        "other-slot.1": active_stream(1, slot=1),
        "other-tg.2": active_stream(1, dst_id=4100),
        "data.2": active_stream(1, is_data=True),
    }
    state.events.append(public_activity_event())

    response = client.get("/api/public/snapshot")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    snapshot = response.json()
    assert snapshot["state"] == {"dashboard": "online", "radio": "online"}
    assert snapshot["metrics"] == {
        "hotspots_connected": 2,
        "tg100_active_hotspots": 1,
        "tg100_transmitting": True,
        "operators_authorized": 1,
    }
    assert snapshot["hotspots"] == [
        {"callsign": "PY2AAA", "connected": True,
         "tg100_active": True, "transmitting": False},
        {"callsign": "PY2ABC", "connected": True,
         "tg100_active": False, "transmitting": True},
    ]
    assert len(snapshot["active_transmissions"]) == 1
    assert snapshot["active_transmissions"][0]["speaker"] == {
        "dmr_id": 724287099, "callsign": "PY2ABC"}
    assert snapshot["active_transmissions"][0]["ingress"]["repeater_id"] == 2
    assert len(snapshot["activity"]) == 1
    assert snapshot["activity"][0]["speaker"]["callsign"] == "PY2ABC"
    assert snapshot["activity"][0]["talkgroup"] == 100
    assert snapshot["activity"][0]["timeslot"] == 2
    assert snapshot["activity"][0]["duration_seconds"] == 4.2
    assert_sanitized_snapshot(snapshot)


@pytest.mark.parametrize("call_type", ["private", "unit", "private_call"])
def test_unit_call_to_numeric_100_is_not_public_tg100_transmission(public_client, call_type):
    client, state = public_client
    state.repeaters = {2: connected_hotspot("PY2ABC")}
    state.streams = {"hotspot.2": active_stream(2, call_type=call_type)}

    snapshot = client.get("/api/public/snapshot").json()

    assert snapshot["metrics"]["tg100_active_hotspots"] == 0
    assert snapshot["metrics"]["tg100_transmitting"] is False
    assert snapshot["hotspots"] == [
        {"callsign": "PY2ABC", "connected": True,
         "tg100_active": False, "transmitting": False}
    ]


def test_assumed_downlink_stream_does_not_mark_hotspot_transmitting(public_client):
    client, state = public_client
    state.repeaters = {2: connected_hotspot("PY2ABC")}
    state.streams = {"hotspot.2": active_stream(2, is_assumed=True)}

    snapshot = client.get("/api/public/snapshot").json()

    assert snapshot["metrics"]["tg100_transmitting"] is False
    assert snapshot["hotspots"][0]["transmitting"] is False


def test_activity_includes_only_public_group_voice_on_ts2_tg100(public_client):
    client, state = public_client
    base_timestamp = int(datetime.now().timestamp())
    same_call = public_activity_event(timestamp=base_timestamp)
    state.events.extend([
        same_call,
        same_call,
        public_activity_event(call_type="private", timestamp=base_timestamp + 1),
        public_activity_event(slot=1, timestamp=base_timestamp + 2),
        public_activity_event(dst_id=4100, timestamp=base_timestamp + 3),
        public_activity_event(is_data=True, timestamp=base_timestamp + 4),
        public_activity_event(is_assumed=True, timestamp=base_timestamp + 5),
    ])

    snapshot = client.get("/api/public/snapshot").json()

    assert len(snapshot["activity"]) == 1
    assert snapshot["activity"][0]["speaker"]["callsign"] == "PY2ABC"
    parsed_timestamp = datetime.fromisoformat(snapshot["activity"][0]["ended_at"])
    assert parsed_timestamp.tzinfo is not None and parsed_timestamp.utcoffset() is not None
    assert_sanitized_snapshot(snapshot)


def test_public_voice_identifies_source_and_allowlisted_hotspot_essid(public_client):
    client, state = public_client
    state.repeaters = {724287002: connected_hotspot("PY2ABC")}
    state.events.append(public_activity_event())

    snapshot = client.get("/api/public/snapshot").json()

    call = snapshot["activity"][0]
    assert call["speaker"] == {"dmr_id": 724287099, "callsign": "PY2ABC"}
    assert call["ingress"] == {
        "repeater_id": 724287002, "callsign": "PY2ABC", "essid": "02"}
    assert "name" not in call["speaker"] and "city" not in call["speaker"]
    assert_sanitized_snapshot(snapshot)


def test_offline_empty_snapshot_and_legacy_apis_are_closed(public_client):
    client, _ = public_client

    snapshot = client.get("/api/public/snapshot").json()

    assert snapshot["state"] == {"dashboard": "online", "radio": "offline"}
    assert snapshot["metrics"] == {
        "hotspots_connected": 0,
        "tg100_active_hotspots": 0,
        "tg100_transmitting": False,
        "operators_authorized": 1,
    }
    assert snapshot["hotspots"] == [] and snapshot["activity"] == []
    assert snapshot["active_transmissions"] == []
    for path in ("/api/config", "/api/repeaters", "/api/streams", "/api/stats"):
        assert client.get(path).status_code == 404


def test_public_site_exposes_only_visitor_page_and_assets(public_client):
    client, _ = public_client

    page = client.get("/")

    assert page.status_code == 200
    assert 'href="/admin"' not in page.text
    assert client.get("/static/dashboard.css").headers["content-type"].startswith("text/css")
    assert client.get("/static/dashboard.js").headers["content-type"].startswith("text/javascript")
    for path in ("/admin", "/admin/login", "/admin/api/operators", "/static/admin.js", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_public_websocket_replays_active_state_and_broadcasts_voice_lifecycle_snapshots(public_client):
    client, state = public_client
    state.repeaters = {2: connected_hotspot("PY2ABC")}
    state.user_db = FakeUserDb()
    state.streams = {"hotspot.2": active_stream(2)}

    with client.websocket_connect("/ws/public") as websocket:
        initial = websocket.receive_json()
        assert initial == {"type": "snapshot", "data": server.build_public_snapshot()}
        assert len(initial["data"]["active_transmissions"]) == 1
        assert_sanitized_snapshot(initial["data"])

        state.repeaters[2]["dynamic_talkgroup_active"] = True
        receiver = server.EventReceiver()
        client.portal.call(receiver.send_to_clients, {
            "type": "stream_start",
            "data": {"repeater_id": 2, "src_id": 724287099, "dst_id": 100,
                     "slot": 2, "call_type": "group", "is_data": False,
                     "is_assumed": False},
        })
        update = websocket.receive_json()

        assert update == {"type": "voice_started", "data": server.build_public_snapshot()}
        assert update["data"]["metrics"]["tg100_active_hotspots"] == 1
        assert update["data"]["metrics"]["tg100_transmitting"] is True
        assert update["data"]["hotspots"][0]["tg100_active"] is True
        assert update["data"]["hotspots"][0]["transmitting"] is True
        assert update["data"]["active_transmissions"][0]["speaker"] == {
            "dmr_id": 724287099, "callsign": "PY2ABC"}
        assert update["data"]["active_transmissions"][0]["ingress"]["repeater_id"] == 2
        assert_sanitized_snapshot(update["data"])

        state.streams["hotspot.2"]["status"] = "hang_time"
        ended = public_activity_event(timestamp=datetime.now().timestamp())
        state.events.append(ended)
        client.portal.call(receiver.send_to_clients, {
            "type": "stream_end", "timestamp": ended["timestamp"], "data": ended["data"]})
        finished = websocket.receive_json()

        assert finished["type"] == "voice_ended"
        assert finished["data"]["active_transmissions"] == []
        assert len(finished["data"]["activity"]) == 1
        assert_sanitized_snapshot(finished["data"])


def test_legacy_raw_websocket_closes_with_policy_violation(public_client):
    client, _ = public_client

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws") as websocket:
            websocket.receive_text()

    assert closed.value.code == 1008


def test_public_websocket_does_not_send_per_second_stream_updates(public_client):
    client, state = public_client

    class Recorder:
        def __init__(self):
            self.messages = []

        async def send_text(self, message):
            self.messages.append(message)

    recorder = Recorder()
    state.public_websocket_clients.add(recorder)

    client.portal.call(server.EventReceiver().send_to_clients, {
        "type": "stream_update",
        "data": {"repeater_id": 724287002, "slot": 2, "duration": 1.0},
    })

    assert recorder.messages == []


def test_https_headers_allow_fetch_and_secure_websocket(public_client):
    client, _ = public_client

    response = client.get("/api/public/snapshot", headers={"x-forwarded-proto": "https"})

    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "connect-src 'self' wss:" in csp
    assert " ws:" not in csp
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
