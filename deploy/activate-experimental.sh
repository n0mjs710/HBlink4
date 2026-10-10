#!/bin/sh
set -eu

root=/home/bueno/HBlink4-experimental-v2
backup=/home/bueno/HBlink4-backup/pre-a5b90e8-20260929
hdrop=/etc/systemd/system/hblink4.service.d/override.conf
ddrop=/etc/systemd/system/hblink4-dash.service.d/override.conf

rollback_on_error() {
    rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ]; then
        rm -f "$hdrop" "$ddrop"
        systemctl daemon-reload || true
        systemctl restart hblink4 hblink4-dash || true
        echo "activation_failed_rolled_back_to_production"
    fi
    exit "$rc"
}

http_status() {
    code=$(curl --connect-timeout 0.5 --max-time 1 -s -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || true)
    [ -n "$code" ] || code=000
    printf '%s' "$code"
}

mark_failed() {
    if [ -z "$failures" ]; then
        failures=$1
    else
        failures="$failures,$1"
    fi
}

[ -s "$backup/config.json" ] && [ -s "$backup/admin.sqlite3" ] && [ -s "$backup/hblink4.service" ] && [ -s "$backup/hblink4-dash.service" ]
[ -s "$root/config/config.json" ] && [ -s "$root/hblink4/hblink.py" ] && [ -s "$root/dashboard/server.py" ]
[ ! -e "$hdrop" ] && [ ! -e "$ddrop" ]
/home/bueno/HBlink4/venv/bin/python -m compileall -q "$root/hblink4" "$root/dashboard"
/home/bueno/HBlink4/venv/bin/python -c 'import json; from pathlib import Path; c=json.loads(Path("/home/bueno/HBlink4-experimental-v2/config/config.json").read_text()); assert c["connection_type_detection"]["strict_hotspot_access"] is True; assert c["global"]["port_ipv4"] == 62031; d=c["dynamic_talkgroups"]; assert d["enabled"] is True and d["talkgroup"] == 100 and d["disconnect_talkgroup"] == 4100'

install -d -m 0755 /etc/systemd/system/hblink4.service.d /etc/systemd/system/hblink4-dash.service.d
install -m 0644 "$root/deploy/systemd/hblink4.override.conf" "$hdrop"
install -m 0644 "$root/deploy/systemd/hblink4-dash.override.conf" "$ddrop"
trap rollback_on_error EXIT
systemctl daemon-reload
systemctl restart hblink4
systemctl restart hblink4-dash

ready=0
last_failed=not_checked
started_at=$(date +%s)
while :; do
    failures=

    if systemctl is-active --quiet hblink4; then :; else mark_failed hblink4_active; fi
    if systemctl is-active --quiet hblink4-dash; then :; else mark_failed hblink4-dash_active; fi

    code=$(http_status http://127.0.0.1:8080/admin/login)
    if [ "$code" = 200 ]; then :; else mark_failed "admin_login_http_${code}"; fi
    code=$(http_status http://127.0.0.1:8081/)
    if [ "$code" = 200 ]; then :; else mark_failed "public_home_http_${code}"; fi
    code=$(http_status http://127.0.0.1:8081/static/dashboard.css)
    if [ "$code" = 200 ]; then :; else mark_failed "public_css_http_${code}"; fi
    code=$(http_status http://127.0.0.1:8081/static/dashboard.js)
    if [ "$code" = 200 ]; then :; else mark_failed "public_js_http_${code}"; fi
    code=$(http_status http://127.0.0.1:8081/api/public/snapshot)
    if [ "$code" = 200 ]; then :; else mark_failed "public_snapshot_http_${code}"; fi
    code=$(http_status http://127.0.0.1:8081/admin)
    if [ "$code" = 404 ]; then :; else mark_failed "public_admin_http_${code}"; fi

    if ss -ltnH 2>/dev/null | grep -Fq '127.0.0.1:8081'; then :; else mark_failed public_loopback_listener; fi
    if ss -lunH 2>/dev/null | grep -q ':62031'; then :; else mark_failed hbp_udp_62031_listener; fi

    if [ -z "$failures" ]; then
        ready=1
        break
    fi
    last_failed=$failures
    now=$(date +%s)
    elapsed=$((now - started_at))
    if [ "$elapsed" -ge 60 ]; then
        break
    fi
    sleep 1
done

if [ "$ready" -ne 1 ]; then
    echo "readiness_timeout_seconds=60" >&2
    echo "readiness_failed_checks=$last_failed" >&2
    exit 1
fi

trap - EXIT
echo 'experimental_services=active'
echo 'admin_lan_login=200'
echo 'public_home_css_js_snapshot=200'
echo 'public_admin=404'
echo 'public_listener=127.0.0.1:8081'
echo 'hbp_listener=udp62031'
echo 'rollback=/home/bueno/HBlink4-experimental-v2/deploy/rollback-production.sh'
