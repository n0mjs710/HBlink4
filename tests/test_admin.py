"""Security and read-only behavior of the BuenoDMR administration area."""

import asyncio
import atexit
import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.admin.apply import BusyError, OperatorManager
from dashboard.admin.operators import config_matches_operators
from hblink4.access_control import RepeaterMatcher
from dashboard.admin.routes import router
from dashboard.admin.security import LoginLimiter, SESSION_COOKIE, password_hash, session_digest
from dashboard.admin.storage import AdminStore


PASSWORD = "a-strong-test-password"
DMR_SECRET = "DMR_SECRET_MUST_NOT_APPEAR"
OPERATORS = [
    ("PY2DES", 724287000, 724287099),
    ("PY2PMI", 724015000, 724015099),
    ("PU2MXD", 724011900, 724011999),
    ("PY2IUD", 724002500, 724002599),
]


@pytest.fixture
def admin_site(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "global": {"bind_ipv4": "127.0.0.1", "port_ipv4": 62031},
        "dynamic_talkgroups": {"enabled": True, "talkgroup": 100, "disconnect_talkgroup": 4100},
        "repeater_configurations": {"patterns": [
            {"name": name, "match": {"id_ranges": [[start, end]]},
             "config": {"passphrase": DMR_SECRET, "trust": False,
                        "slot1_talkgroups": [], "slot2_talkgroups": [100]}}
            for name, start, end in OPERATORS
        ]}
    }), encoding="utf-8")
    original_config = config_path.read_bytes()
    store = AdminStore(tmp_path / "data")
    store.initialize()
    app = FastAPI()
    app.include_router(router)
    app.state.admin_store = store
    app.state.admin_limiter = LoginLimiter()
    app.state.admin_hash_semaphore = asyncio.Semaphore(2)
    app.state.admin_dummy_hash = password_hash("dummy-password-never-used")
    manager = OperatorManager(store, config_path, tmp_path / "backup", restarter=SimpleNamespace(restart=lambda: None))
    manager.initialize()
    app.state.admin_manager = manager
    app.state.admin_operator_error = False
    with TestClient(app) as client:
        yield client, store, config_path, original_config


def csrf_from(page):
    return re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)


def bootstrap(store):
    store.create_or_replace_admin("PY2DES", 7242870, password_hash(PASSWORD))


def sign_in(client, password=PASSWORD, username="PY2DES"):
    login_page = client.get("/admin/login")
    return client.post("/admin/login", data={
        "username": username, "password": password, "csrf": csrf_from(login_page)
    }, follow_redirects=False)


def test_protected_admin_and_public_dashboard(admin_site):
    client, store, _, _ = admin_site
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/admin/login"
    assert client.get("/admin/login").status_code == 200
    assert client.get("/admin/login").headers["cache-control"] == "no-store"

    from dashboard import server as dashboard_module
    atexit.unregister(dashboard_module.save_persistent_data)
    dashboard_app = dashboard_module.app
    public_client = TestClient(dashboard_app)
    assert public_client.get("/").status_code == 200
    assert public_client.get("/api/stats").status_code == 404
    assert public_client.get("/api/public/snapshot").status_code == 200


def test_login_session_logout_and_audit(admin_site):
    client, store, _, _ = admin_site
    bootstrap(store)
    invalid = sign_in(client, "wrong-password")
    assert invalid.status_code == 401
    assert "Usuário ou senha inválidos." in invalid.text
    assert client.get("/admin", follow_redirects=False).status_code == 303

    success = sign_in(client)
    assert success.status_code == 303
    assert success.headers["location"] == "/admin"
    cookie = success.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=" in cookie
    home = client.get("/admin")
    assert home.status_code == 200 and "PY2DES" in home.text
    assert client.get("/admin/").status_code == 200
    assert client.post("/admin/logout", data={"csrf": "wrong"}).status_code == 403
    assert client.get("/admin").status_code == 200
    logout = client.post("/admin/logout", data={"csrf": csrf_from(home)}, follow_redirects=False)
    assert logout.status_code == 303
    assert client.get("/admin", follow_redirects=False).status_code == 303
    with sqlite3.connect(store.db_path) as db:
        actions = [row[0] for row in db.execute("SELECT action FROM audit_log ORDER BY id")]
        assert actions == ["admin_bootstrap", "admin_login_failure", "admin_login_success", "admin_logout"]
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_hash_bootstrap_identity_and_no_automatic_admins(admin_site):
    _, store, _, _ = admin_site
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0
    bootstrap(store)
    admin = store.get_admin("PY2DES")
    assert admin["dmr_id"] == 7242870 and admin["role"] == "admin"
    assert admin["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in admin["password_hash"]
    for name, _, _ in OPERATORS[1:]:
        assert store.get_admin(name) is None
    with pytest.raises(ValueError):
        store.create_or_replace_admin("PY2DES", 7242870, password_hash(PASSWORD))
    with pytest.raises(ValueError):
        store.create_or_replace_admin("PY2PMI", 7240150, password_hash(PASSWORD))


def test_csrf_login_and_logout_reject_invalid_tokens(admin_site):
    client, store, _, _ = admin_site
    bootstrap(store)
    assert client.post("/admin/login", data={
        "username": "PY2DES", "password": PASSWORD, "csrf": "invalid"
    }).status_code == 403
    assert client.get("/admin", follow_redirects=False).status_code == 303
    assert sign_in(client).status_code == 303
    assert client.post("/admin/logout", data={"csrf": "invalid"}).status_code == 403
    assert client.post("/admin/logout", data={"csrf": csrf_from(client.get("/admin"))}).status_code == 200


def test_rate_limit_is_bounded_and_rejects_correct_password(admin_site):
    client, store, _, _ = admin_site
    bootstrap(store)
    for _ in range(5):
        assert sign_in(client, "wrong-password").status_code == 401
    assert sign_in(client).status_code == 429
    assert len(client.app.state.admin_limiter.attempts) <= 1024


def test_pattern_projection_and_config_remain_unchanged_on_read(admin_site):
    client, store, config_path, original_config = admin_site
    bootstrap(store)
    assert sign_in(client).status_code == 303
    operators = client.get("/admin/api/operators").json()["operators"]
    assert [(o["callsign"], o["base_id"], o["essid_from"], o["essid_to"], o["range_start"], o["range_end"]) for o in operators] == [
        (name, start // 100, 0, 99, start, end) for name, start, end in OPERATORS
    ]
    page = client.get("/admin")
    for name, start, end in OPERATORS:
        assert name in page.text and f"{start}-{end}" in page.text
    assert "Adicionar operador" in page.text
    assert DMR_SECRET not in page.text
    assert DMR_SECRET not in json.dumps(operators)
    assert PASSWORD not in page.text
    assert config_path.read_bytes() == original_config


def test_browser_admin_api_contract_and_csp(admin_site):
    client, store, _, _ = admin_site
    bootstrap(store)
    assert sign_in(client).status_code == 303
    page = client.get("/admin")
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert '/static/admin.js?v=2' in page.text
    token = re.search(r'data-csrf="([^"]+)"', page.text).group(1)
    assert token

    from dashboard import server as dashboard_module
    atexit.unregister(dashboard_module.save_persistent_data)
    asset = TestClient(dashboard_module.app).get("/static/admin.js?v=2")
    assert asset.status_code == 200
    assert 'apiRequest("/admin/api/operators")' in asset.text
    assert '"X-CSRF-Token": csrf' in asset.text
    assert 'credentials: "same-origin"' in asset.text

    response = client.get("/admin/api/operators")
    assert response.status_code == 200
    operators = response.json()["operators"]
    assert [row["callsign"] for row in operators] == [name for name, _, _ in OPERATORS]
    assert all(type(row["id"]) is int and row["id"] > 0 for row in operators)
    assert DMR_SECRET not in response.text
    first = operators[0]
    assert client.post(f'/admin/api/operators/{first["id"]}/disable', json={},
                       headers={"X-CSRF-Token": "invalid"}).json() == {"error": "Invalid CSRF token."}
    invalid = client.post("/admin/api/operators", json={"callsign": "BAD", "base_id": 123},
                          headers={"X-CSRF-Token": token})
    assert invalid.status_code == 422 and "error" in invalid.json()
    assert client.get("/admin/api/audit").status_code == 200


def test_operator_api_requires_live_session(admin_site):
    client, _, _, _ = admin_site
    assert client.get("/admin/api/operators").status_code == 401
    assert client.post("/admin/api/operators", json={"callsign": "PY2ABC", "base_id": 7249999},
                       headers={"X-CSRF-Token": "invalid"}).status_code == 401


def test_config_drift_refuses_changes(admin_site):
    client, store, config_path, _ = admin_site
    config = json.loads(config_path.read_text())
    config["repeater_configurations"]["patterns"].append({"name": "BAD", "match": {"ids": [312001]}})
    config_path.write_text(json.dumps(config), encoding="utf-8")
    bootstrap(store)
    assert sign_in(client).status_code == 303
    assert client.post("/admin/api/operators", json={"callsign": "PY2ABC", "base_id": 7249999}).status_code == 403


def test_session_survives_store_reopen_and_secure_cookie_option(admin_site, monkeypatch):
    client, store, _, _ = admin_site
    bootstrap(store)
    monkeypatch.setenv("BUENODMR_ADMIN_COOKIE_SECURE", "1")
    https_client = TestClient(client.app, base_url="https://testserver")
    success = sign_in(https_client)
    assert success.status_code == 303
    assert "Secure" in success.headers["set-cookie"]
    token = success.headers["set-cookie"].split(f"{SESSION_COOKIE}=", 1)[1].split(";", 1)[0]
    reopened = AdminStore(store.data_dir)
    reopened.initialize()
    assert reopened.secret == store.secret
    assert reopened.get_session_admin(session_digest(reopened.secret, token))["username"] == "PY2DES"


def test_cli_bootstrap_only_py2des_and_requires_explicit_rotation(admin_site, monkeypatch):
    from dashboard import admin_cli

    _, store, _, _ = admin_site
    monkeypatch.setattr(admin_cli, "AdminStore", lambda _path: store)
    monkeypatch.setattr(admin_cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    answers = iter((PASSWORD, PASSWORD))
    monkeypatch.setattr(admin_cli.getpass, "getpass", lambda _prompt: next(answers))
    with pytest.raises(SystemExit):
        admin_cli.main(["create-admin", "--username", "PY2PMI", "--dmr-id", "7240150"])
    admin_cli.main(["create-admin", "--username", "PY2DES", "--dmr-id", "7242870"])
    assert store.get_admin("PY2DES") is not None
    with pytest.raises(SystemExit):
        admin_cli.main(["create-admin", "--username", "PY2DES", "--dmr-id", "7242870"])


def mutation(client, method, path, payload, csrf=None):
    page = client.get("/admin")
    token = re.search(r'data-csrf="([^"]+)"', page.text).group(1) if csrf is None else csrf
    return client.request(method, path, json=payload, headers={"X-CSRF-Token": token})


def test_crud_config_policy_and_secret_isolation(admin_site):
    client, store, config_path, original = admin_site
    bootstrap(store)
    assert sign_in(client).status_code == 303
    original_json = json.loads(original)
    created = mutation(client, "POST", "/admin/api/operators", {"callsign": "py2abc", "base_id": 7249999})
    assert created.status_code == 200
    items = created.json()["operators"]
    added = next(row for row in items if row["callsign"] == "PY2ABC")
    assert (added["range_start"], added["range_end"]) == (724999900, 724999999)
    oid = added["id"]
    config = json.loads(config_path.read_text())
    assert config["dynamic_talkgroups"] == original_json["dynamic_talkgroups"]
    assert "default" not in config["repeater_configurations"]
    patterns = config["repeater_configurations"]["patterns"]
    assert len(patterns) == 5
    assert all(p["config"] == {"passphrase": DMR_SECRET, "trust": False,
                                "slot1_talkgroups": [], "slot2_talkgroups": [100]} for p in patterns)
    matcher = RepeaterMatcher(config)
    assert matcher.get_repeater_config(724999900, "PY2ABC") is not None
    assert matcher.get_repeater_config(724999899, "PY2ABC") is None
    assert config_matches_operators(config, store.list_operators())
    assert len(list(client.app.state.admin_manager.backup_dir.glob("config-*.json.bak"))) == 1
    edited = mutation(client, "PUT", f"/admin/api/operators/{oid}", {
        "callsign": "PY2XYZ", "base_id": 7248888, "essid_from": 3, "essid_to": 5, "active": True})
    assert edited.status_code == 200
    assert next(row for row in edited.json()["operators"] if row["id"] == oid)["range_start"] == 724888803
    disabled = mutation(client, "POST", f"/admin/api/operators/{oid}/disable", {})
    assert disabled.status_code == 200
    assert not next(row for row in disabled.json()["operators"] if row["id"] == oid)["active"]
    assert RepeaterMatcher(json.loads(config_path.read_text())).get_repeater_config(724888803, "PY2XYZ") is None
    enabled = mutation(client, "POST", f"/admin/api/operators/{oid}/enable", {})
    assert enabled.status_code == 200
    assert RepeaterMatcher(json.loads(config_path.read_text())).get_repeater_config(724888803, "PY2XYZ") is not None
    assert mutation(client, "DELETE", f"/admin/api/operators/{oid}", {"confirm_callsign": "wrong"}).status_code == 422
    deleted = mutation(client, "DELETE", f"/admin/api/operators/{oid}", {"confirm_callsign": "PY2XYZ"})
    assert deleted.status_code == 200
    assert len(deleted.json()["operators"]) == 4
    for response in (created, edited, disabled, enabled, deleted, client.get("/admin/api/operators"), client.get("/admin/api/audit"), client.get("/admin")):
        assert DMR_SECRET not in response.text
    assert DMR_SECRET not in store.db_path.read_bytes().decode("latin1")
    assert {row["callsign"] for row in store.list_operators()} == {name for name, _, _ in OPERATORS}
    actions = [row["action"] for row in store.recent_audit(20)]
    for action in ("operator_created", "operator_updated", "operator_disabled", "operator_enabled", "operator_deleted", "config_apply_success"):
        assert action in actions


@pytest.mark.parametrize("payload", [
    {"callsign": "PY2DES", "base_id": 7249999},
    {"callsign": "PY2ABC", "base_id": 7242870},
    {"callsign": "PY2ABC", "base_id": 999999},
    {"callsign": "BAD<script>", "base_id": 7249999},
    {"callsign": "PY2ABC", "base_id": 7249999, "essid_from": 99, "essid_to": 0},
    {"callsign": "PY2ABC", "base_id": 7249999, "essid_from": -1},
    {"callsign": "PY2ABC", "base_id": 7249999, "trust": True},
])
def test_validation_leaves_config_and_db_unchanged(admin_site, payload):
    client, store, config_path, original = admin_site
    bootstrap(store)
    sign_in(client)
    assert mutation(client, "POST", "/admin/api/operators", payload).status_code == 422
    assert config_path.read_bytes() == original
    assert len(store.list_operators()) == 4


def test_auth_csrf_role_and_rollback(admin_site):
    client, store, config_path, original = admin_site
    endpoint = "/admin/api/operators"
    payload = {"callsign": "PY2ABC", "base_id": 7249999}
    assert client.post(endpoint, json=payload).status_code == 401
    bootstrap(store)
    sign_in(client)
    assert mutation(client, "POST", endpoint, payload, csrf="invalid").status_code == 403
    csrf = re.search(r'data-csrf="([^"]+)"', client.get("/admin").text).group(1)
    with sqlite3.connect(store.db_path) as db:
        db.execute("UPDATE admins SET role='operator' WHERE username='PY2DES'")
    assert client.post(endpoint, json=payload, headers={"X-CSRF-Token": csrf}).status_code == 403
    with sqlite3.connect(store.db_path) as db:
        db.execute("UPDATE admins SET role='admin' WHERE username='PY2DES'")
    calls = []
    def fail():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated restart failure")
    client.app.state.admin_manager.restarter = SimpleNamespace(restart=fail)
    response = mutation(client, "POST", endpoint, payload)
    assert response.status_code == 503
    assert response.json() == {"error": "Failed to apply configuration. Previous configuration restored."}
    assert len(calls) == 2
    assert config_path.read_bytes() == original
    assert len(store.list_operators()) == 4
    assert not client.app.state.admin_manager.marker_path.exists()
    actions = [row["action"] for row in store.recent_audit(10)]
    assert "config_apply_failure" in actions


def test_radio_access_policy_admin_toggle_persistence_and_audit(admin_site):
    client, store, config_path, original = admin_site
    bootstrap(store)
    assert client.get("/admin/api/radio-access").status_code == 401
    assert sign_in(client).status_code == 303

    observed = {"hblink_connected": True, "clients": [{
        "repeater_id": 724287002, "callsign": "PY2DES", "connection_type": "hotspot",
        "software_id": "20260911_WPSD", "package_id": "MMDVM_MMDVM_HS_Dual_Hat",
    }]}
    client.app.state.radio_access_observer = lambda: observed
    status = client.get("/admin/api/radio-access")
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["allowed_profiles"] == ["hotspot"]
    assert status.json()["ready_to_enable"] is True
    assert status.json()["clients"][0]["package_id"] == "MMDVM_MMDVM_HS_Dual_Hat"

    # Exact CSRF and explicit confirmation are required for either direction.
    assert client.post("/admin/api/radio-access", json={"enabled": False,
        "confirmation": "DESATIVAR"}).status_code == 403
    disabled = mutation(client, "POST", "/admin/api/radio-access", {
        "enabled": False, "confirmation": "ATIVAR"})
    assert disabled.status_code == 422
    disabled = mutation(client, "POST", "/admin/api/radio-access", {
        "enabled": False, "confirmation": "DESATIVAR"})
    assert disabled.status_code == 200
    changed = json.loads(config_path.read_text())
    assert changed["connection_type_detection"]["strict_hotspot_access"] is False
    assert store.get_meta("strict_hotspot_access") == "disabled"
    assert {(row["callsign"], row["base_id"], row["essid_from"], row["essid_to"], row["active"])
            for row in store.list_operators()} == {
        (name, start // 100, 0, 99, True) for name, start, _ in OPERATORS
    }

    observed["clients"][0]["connection_type"] = "unknown"
    refused = mutation(client, "POST", "/admin/api/radio-access", {
        "enabled": True, "confirmation": "ATIVAR"})
    assert refused.status_code == 409
    assert json.loads(config_path.read_text())["connection_type_detection"]["strict_hotspot_access"] is False

    observed["clients"][0]["connection_type"] = "hotspot"
    enabled = mutation(client, "POST", "/admin/api/radio-access", {
        "enabled": True, "confirmation": "ATIVAR"})
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True
    assert json.loads(config_path.read_text())["connection_type_detection"]["strict_hotspot_access"] is True
    actions = [row["action"] for row in store.recent_audit(20)]
    assert "hotspot_access_disabled" in actions
    assert "hotspot_access_enabled" in actions
    assert config_path.read_bytes() != original
    assert DMR_SECRET not in client.get("/admin/api/radio-access").text


def test_radio_access_toggle_restores_policy_on_restart_failure(admin_site):
    client, store, config_path, original = admin_site
    bootstrap(store)
    sign_in(client)
    client.app.state.radio_access_observer = lambda: {
        "hblink_connected": True,
        "clients": [{"connection_type": "hotspot", "callsign": "PY2DES"}],
    }
    calls = []

    def fail_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated restart failure")

    client.app.state.admin_manager.restarter = SimpleNamespace(restart=fail_once)
    response = mutation(client, "POST", "/admin/api/radio-access", {
        "enabled": False, "confirmation": "DESATIVAR"})
    assert response.status_code == 503
    assert len(calls) == 2
    assert config_path.read_bytes() == original
    assert store.get_meta("strict_hotspot_access") == "enabled"
    assert not client.app.state.admin_manager.marker_path.exists()
    actions = [row["action"] for row in store.recent_audit(10)]
    assert "hotspot_access_disabled" in actions
    assert "config_apply_failure" in actions
    assert "config_rollback" in actions


def test_pending_radio_access_apply_recovers_previous_policy(admin_site):
    client, store, config_path, original = admin_site
    manager = client.app.state.admin_manager
    backup = manager._backup(original)
    manager._write_marker(backup, {"previous": True, "target": False})
    candidate = json.loads(original)
    candidate["connection_type_detection"] = {"strict_hotspot_access": False}
    manager._expected_config_bytes = original
    manager._replace_config(json.dumps(candidate).encode())

    manager.initialize()

    assert config_path.read_bytes() == original
    assert store.get_meta("strict_hotspot_access") == "enabled"
    assert not manager.marker_path.exists()


def test_client_admission_audit_is_sanitized(admin_site):
    _, store, _, _ = admin_site
    store.record_client_admission_rejected(
        724287002, "PY2DES", "unknown", "profile_not_allowed",
        "2026-09-29T12:00:00+00:00",
    )
    row = store.recent_audit(1)[0]
    assert row["action"] == "client_admission_rejected"
    assert row["repeater_id"] == 724287002
    assert row["operator_callsign"] == "PY2DES"
    assert row["classification"] == "unknown"
    assert row["reason"] == "profile_not_allowed"
    assert "client_ip" not in row and "passphrase" not in row


def test_migration_idempotent_and_admin_independent(admin_site):
    client, store, config_path, _ = admin_site
    assert [(r["callsign"], r["base_id"]) for r in store.list_operators()] == [
        (name, start // 100) for name, start, _ in OPERATORS]
    assert store.import_operators(config_path) is False
    assert len(store.list_operators()) == 4
    assert DMR_SECRET not in store.db_path.read_bytes().decode("latin1")
    bootstrap(store)
    sign_in(client)
    item = store.list_operators()[0]
    assert mutation(client, "POST", f'/admin/api/operators/{item["id"]}/disable', {}).status_code == 200
    assert store.get_admin("PY2DES")["role"] == "admin"
    assert client.get("/admin").status_code == 200
    assert store.import_operators(config_path) is False
    assert len(store.list_operators()) == 4


def test_concurrent_apply_rejected(admin_site):
    client, store, config_path, original = admin_site
    manager = client.app.state.admin_manager
    manager._thread_lock.acquire()
    try:
        with pytest.raises(BusyError):
            manager.apply("create", None, {"callsign": "PY2ABC", "base_id": 7249999},
                          {"id": 1, "username": "PY2DES"})
    finally:
        manager._thread_lock.release()
    assert config_path.read_bytes() == original
    assert len(store.list_operators()) == 4


def test_pending_apply_recovers_original_config(admin_site):
    client, store, config_path, original = admin_site
    manager = client.app.state.admin_manager
    backup = manager._backup(original)
    manager._write_marker(backup)
    changed = json.loads(original)
    changed["repeater_configurations"]["patterns"].pop()
    manager._expected_config_bytes = original
    manager._replace_config(json.dumps(changed).encode())
    manager.initialize()
    assert config_path.read_bytes() == original
    assert not manager.marker_path.exists()
