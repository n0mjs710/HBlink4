# BuenoDMR administration

The existing dashboard remains public at `/`. The separate `/admin` area requires
an administrative account. The DMR network and the admin area use different
credentials. A DMR callsign, base ID, ESSID, hotspot connection, and the shared
DMR passphrase do not grant dashboard access.

Phase 2 manages the DMR allowlist through `/admin`. The existing administrative
identity is independent of DMR authorization. The browser can create, edit,
enable, disable, and delete operators, but cannot edit arbitrary JSON. Deletion
requires explicit callsign confirmation. The public dashboard remains at `/`.

## Operator persistence and migration

On first startup after this update, the dashboard transactionally imports the
existing, unambiguous `repeater_configurations.patterns` into the SQLite
`operators` table. Import is marked complete even if the table later becomes
empty; it is never silently repeated. If the patterns are not representable as
single base-ID ESSID ranges with one shared passphrase and TS2/TG100 policy,
operator management remains unavailable until an administrator resolves the
configuration. SQLite then becomes the allowlist source of truth. External
edits to the generated ACL cause a conflict rather than being overwritten.
The shared DMR passphrase is never stored in SQLite or sent to the browser.

Operators have a normalized callsign, seven-digit base DMR ID, ESSID bounds
`00..99`, and an active flag. New operators default to `00..99` and active.
Only active operators are written as patterns. The generated section has no
`default`, `trust=false`, TS1 empty, and TS2 limited to TG100. The current
shared passphrase stays exclusively in the real `config/config.json`; the
private `_bueno_shared_passphrase` member retains it when the last operator is
disabled or removed. HBlink4's matcher ignores this member. Other config
sections, including dynamic TG100/TG4100, are preserved.

## Apply and recovery

Each update requires an admin session, role, and CSRF token. The dashboard
validates all fields, checks config/DB consistency, and serializes writers
with a process lock, file lock on Linux, and SQLite `BEGIN IMMEDIATE`. It
validates the complete candidate with the HBlink4 repeater matcher, makes a
timestamped backup, fsyncs a staged file, and atomically replaces the config.
It then signals the fixed `hblink4` service process and waits for systemd's
`Restart=always` policy to start a stable replacement. Only then does it
commit the operator rows and audit event. A failure restores the prior config,
restarts HBlink4 again, and rolls back SQLite. A pending marker enables
recovery after a dashboard crash. The dashboard service and hblink4 service
must run as the same unprivileged user; a changed unit owner or restart policy
causes a closed failure. The browser cannot supply a service name or command.

Config backups live outside Git at `~/HBlink4-backup/buenodmr-config`, mode
`0600`; the last 20 are retained. They contain the live DMR passphrase and
must be protected accordingly. Audit records operator actions, apply success,
failure, and rollback with callsign and base ID, never credentials.

## Strict Hotspot Access

The Admin page shows the current server-side HBP classification for connected
clients and exposes **Acesso exclusivo por Hotspot**. BuenoDMR defaults this
policy to enabled. Enabling requires a connected HBlink4 process and every
current HBP session to classify as `hotspot`; `unknown`, network, or repeater
profiles block activation. A change requires an Admin session, CSRF token, and
exact `ATIVAR` or `DESATIVAR` confirmation. It updates only
`connection_type_detection.strict_hotspot_access`, persists a mirror in
`admin_meta`, backs up the config, restarts HBlink4, and restores the previous
config if application fails. Active DMR clients may reconnect during this
restart.

The accepted `hotspot` label is derived from RPTC Package ID / Software ID
metadata supplied by each client. It is an operational filter, not proof of
physical hotspot hardware; a compatible client that knows a valid DMR identity
and passphrase can imitate those strings. The policy does not affect public
browser access. Rejection events record timestamp, repeater ID, validated
callsign when available, classification, and a fixed reason, without IP or
authentication material. See [DMR access checks and origin limits](radio-access-security.md).

## First administrator

Install `requirements-dashboard.txt` into the dashboard's existing virtual
environment, then run this command **interactively** on the server:

```sh
cd ~/HBlink4
./venv/bin/python -m dashboard.admin_cli create-admin --username PY2DES --dmr-id 7242870
```

The CLI prompts twice for a password of at least 12 characters and never echoes
it. No account is created by dashboard startup or deployment. Phase 1 permits
only PY2DES (base ID 7242870) as an admin. The other authorized DMR operators
receive no admin account. To rotate the password deliberately, rerun the
command with `--replace` and type `PY2DES` at the confirmation prompt. Rotation
invalidates existing sessions.

## Storage and security

`dashboard/data/admin.sqlite3` holds admin accounts, Argon2id password hashes,
server-side session hashes, and audit entries. The schema is initialized
idempotently. `dashboard/data/admin_session.key` is a persistent random HMAC
key for session and CSRF tokens. Both runtime files are ignored by Git and are
created with owner-only permissions on Linux. The key must remain private.

Sessions use random opaque cookies, expire after eight hours, and are deleted
on logout. Cookies are HttpOnly and SameSite=Lax. Login and logout forms require
signed CSRF tokens. Failed login attempts are limited by account and client IP
with bounded in-memory state. The audit log records login successes, failures,
logouts, and initial account creation without passwords, hashes, session tokens,
or DMR passphrases. Audit retention is capped at 10,000 entries.

The current LAN HTTP deployment uses cookies without `Secure`, since that flag
would prevent login over HTTP. Set `BUENODMR_ADMIN_COOKIE_SECURE=1` in the
dashboard service environment when the browser connects through HTTPS. HTTP
does not protect credentials from a network observer; do not expose `/admin`
outside the trusted LAN without HTTPS.

## Backup

Back up both files outside the checkout with restrictive permissions. SQLite's
backup API creates a consistent copy even while the dashboard is running:

```sh
cd ~/HBlink4
./venv/bin/python - <<'PY'
import datetime, os, pathlib, shutil, sqlite3
source = pathlib.Path('dashboard/data')
target = pathlib.Path.home() / 'HBlink4-backup' / ('admin-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
target.mkdir(parents=True, mode=0o700)
with sqlite3.connect(source / 'admin.sqlite3') as db, sqlite3.connect(target / 'admin.sqlite3') as copy:
    db.backup(copy)
os.chmod(target / 'admin.sqlite3', 0o600)
shutil.copyfile(source / 'admin_session.key', target / 'admin_session.key')
os.chmod(target / 'admin_session.key', 0o600)
print(target)
PY
```

Protect the backup like an administrative credential. To restore, stop only the
dashboard, restore the database and key together with owner-only permissions,
and start the dashboard. Also keep the matching config backup so SQLite and
the applied ACL can be recovered together.
