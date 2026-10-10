# HBlink4 Configuration Guide

HBlink4 uses a JSON configuration file (`config/config.json`) to define server settings, repeater access control rules, and talkgroup routing. This guide explains each configuration section and its options.

## Configuration File Structure

The HBlink4 server configuration file consists of five main sections:
- **Global Settings** - Server-wide settings
- **Dashboard** - Web dashboard and event communication
- **Blacklist Rules** - Access control for blocking repeaters
- **Repeater Configurations** - Per-repeater authentication and routing
- **Network Outbound** - Server-to-server links (optional)

## Global Settings

The `global` section contains server-wide settings that control the basic operation of HBlink4.

```json
{
    "global": {
        "max_missed": 3,
        "timeout_duration": 30,
        "reconnect_report_threshold": 3600,
        "bind_ipv4": "0.0.0.0",
        "port_ipv4": 62031,
        "bind_ipv6": "::",
        "port_ipv6": 62032,
        "disable_ipv6": true,
        "logging": {
            "file": "logs/hblink.log",
            "console_level": "INFO",
            "file_level": "DEBUG",
            "retention_days": 30
        },
        "stream_timeout": 2.0,
        "stream_hang_time": 10.0,
        "user_cache": {
            "timeout": 600
        }
    }
}
```

| Setting | Type | Description |
|---------|------|-------------|
| `max_missed` | number | Maximum consecutive missed pings before disconnecting a repeater (default: 3) |
| `timeout_duration` | number | Seconds between expected pings from repeaters (default: 30) |
| `reconnect_report_threshold` | number | When a repeater logs in again, the log reports how long its previous session lasted. Sessions longer than this many seconds are routine and are not reported; shorter ones are. Set to `0` to report every reconnect (default: 3600). Connections are always logged regardless of this setting |
| `disable_ipv6` | boolean | Bind IPv4 only. The shipped sample config sets this to `true`, which is what most networks want; if the setting is absent altogether the built-in default is `false` |
| `bind_ipv4` | string | IPv4 address to bind ("0.0.0.0" for all IPv4 interfaces) |
| `bind_ipv6` | string | IPv6 address to bind ("::" for all IPv6 interfaces) |
| `port_ipv4` | number | UDP port for IPv4 (default: 62031) |
| `port_ipv6` | number | UDP port for IPv6 (default: 62031) |
| `logging.file` | string | Path to log file |
| `logging.console_level` | string | Logging level for console output ("DEBUG", "INFO", "WARNING", "ERROR") |
| `logging.file_level` | string | Logging level for file output ("DEBUG", "INFO", "WARNING", "ERROR") |
| `logging.retention_days` | number | Number of days to retain log files (default: 30) |
| `stream_timeout` | float | Fallback timeout when terminator frame is lost (default: 2.0 seconds) |
| `stream_hang_time` | float | Seconds to reserve slot for same source after stream ends (default: 10.0-20.0 seconds) |
| `user_cache.timeout` | number | Seconds before user cache entries expire (default: 600, minimum: 60) |

**Note on IPv6**: HBlink4 is dual-stack native and supports IPv4-only, IPv6-only, and dual-stack operation. The sample config ships IPv4-only (`disable_ipv6: true`), since that is what most repeaters use and it requires no decisions to get started. To run IPv6 or dual-stack, see [Dual-Stack IPv6 Support](#dual-stack-ipv6-support) below.

**User Cache**: The user cache tracks the last known repeater for each DMR ID to enable efficient private call routing. Entries are automatically cleaned up every 60 seconds. The timeout must be at least 60 seconds.

> ⚠️ **Don't set `user_cache.timeout` shorter than your longest expected transmission.** A cache entry's `last_heard` is refreshed only at stream start (PTT), not on every voice packet, so a transmission that outlasts the timeout will leave the talker's entry expired by the time they unkey — even though they were clearly active the whole time. DMR transmissions can run 2–3 minutes, so keep the timeout well above that. The 600-second default comfortably covers normal operation; the 60-second minimum is permitted but only appropriate for testing or very short-TX environments. Setting it too low degrades unit-call routing by forcing unnecessary broadcasts immediately after long transmissions.

### Dual-Stack IPv6 Support

HBlink4 is **dual-stack native**. IPv4, IPv6, and both at once are all fully
supported; the choice is yours and none of them is a second-class path.

The sample config ships IPv4-only (`disable_ipv6: true`) because that is what
the overwhelming majority of repeaters and hotspots use today, and because it
needs no decisions from a first-time operator. If you have IPv6 you intend to
serve, enabling it is a supported, tested configuration — set
`disable_ipv6: false` and pick one of the layouts below.

**Each address family gets its own UDP socket.** That is the one mechanical
detail worth knowing before you enable IPv6, because it determines which of the
layouts below you want.

- `bind_ipv4` / `port_ipv4` configure the IPv4 socket; `""` means no IPv4 socket
- `bind_ipv6` / `port_ipv6` configure the IPv6 socket; `disable_ipv6: true` means no IPv6 socket
- Either can use a wildcard (`"0.0.0.0"`, `"::"`) or a specific address (`"192.168.1.10"`, `"2001:db8::1"`)

#### Choosing a layout

**IPv4 only** — the shipped default.

```json
"bind_ipv4": "0.0.0.0",
"port_ipv4": 62031,
"disable_ipv6": true
```

**Dual-stack, separate ports** — the simplest way to add IPv6. Two independent
sockets, no interaction between them. Repeaters connecting over IPv6 must be
pointed at `port_ipv6`. The sample config is pre-set for this, so enabling IPv6
is a one-key change.

```json
"bind_ipv4": "0.0.0.0",
"port_ipv4": 62031,
"bind_ipv6": "::",
"port_ipv6": 62032,
"disable_ipv6": false
```

**Dual-stack, one socket, one port** — set `bind_ipv4` to `""` so only the IPv6
socket binds. On Linux (`net.ipv6.bindv6only=0`, the default) that socket serves
IPv4 as well, with IPv4 peers appearing as `::ffff:` mapped addresses. Every
repeater uses the same port regardless of family. This is also the configuration
to use on an IPv6-only host. It requires kernel IPv6, so it fails outright on a
host booted with `ipv6.disable=1`.

```json
"bind_ipv4": "",
"bind_ipv6": "::",
"port_ipv6": 62031,
"disable_ipv6": false
```

**Dual-stack on specific addresses** — bind each family to a real address rather
than a wildcard. The two sockets no longer overlap, so they can share a port.

```json
"bind_ipv4": "192.168.1.10",
"port_ipv4": 62031,
"bind_ipv6": "2001:db8::1",
"port_ipv6": 62031,
"disable_ipv6": false
```

#### If you see "address already in use" on the IPv6 bind

You have both families on wildcards sharing one port — the IPv4 socket holds it
and the IPv6 bind cannot also claim it. This is **normal Linux behavior**, not a
fault in your network or in HBlink4. The server keeps running on IPv4 and logs
the conflict along with the fix. Pick any of the three dual-stack layouts above.

## Dashboard Configuration

**Who connects to whom:** the dashboard **listens**, HBlink4 **connects out to it** and pushes events. HBlink4 is the client on this link; it retries every 10 seconds until the dashboard answers. The dashboard may send a `sync_request` back over that same connection, which HBlink4 answers with a full state dump — but it never dials HBlink4.

**Naming convention:** throughout HBlink4's configuration files,

| Prefix | Meaning | Wildcards |
|--------|---------|-----------|
| `bind_*` / `bind` | an address **we listen on** | `0.0.0.0`, `::`, or `""` to disable that family |
| `host_*` | an address **we connect out to** | never a wildcard — it must be reachable |

The `dashboard` section is a **top-level** configuration (not nested under `global`) and tells HBlink4 **where to find the dashboard**. The dashboard's own listening addresses live in its `event_receiver` section (see [Dashboard-Side Configuration](#dashboard-side-configuration) below).

```json
{
    "dashboard": {
        "enabled": true,
        "disable_ipv6": false,
        "transport": "unix",
        "host_ipv4": "127.0.0.1",
        "host_ipv6": "::1",
        "port": 8765,
        "unix_socket": "/tmp/hblink4.sock",
        "buffer_size": 65536
    }
}
```

| Setting | Type | Description |
|---------|------|-------------|
| `enabled` | boolean | Enable/disable dashboard event emitting |
| `disable_ipv6` | boolean | Disable IPv6 for dashboard (independent of global setting) |
| `transport` | string | Transport type: `"unix"` or `"tcp"` (see below) |
| `host_ipv4` | string | IPv4 address of the dashboard to connect to (e.g., "127.0.0.1"). Not a bind address — wildcards like "0.0.0.0" are invalid here |
| `host_ipv6` | string | IPv6 address of the dashboard to connect to (e.g., "::1"). Not a bind address — wildcards like "::" are invalid here |
| `port` | number | Port the dashboard is listening on (default: 8765) |
| `unix_socket` | string | Path of the Unix socket the dashboard created (default: "/tmp/hblink4.sock") |
| `buffer_size` | number | Socket send buffer size (default: 65536) |

### Transport Options

**Unix Socket (`"unix"`)** - Recommended for local dashboard:
- ✅ Fastest performance (~0.5-1μs per event)
- ✅ Same-host only (most secure)
- ✅ Automatic cleanup on startup
- ✅ File permissions control access
- **Use when**: Dashboard runs on same server as HBlink4
- **Configuration**: Only `unix_socket` path is used (host and port fields ignored). The dashboard creates the socket file; HBlink4 connects to it, so both sides must name the same path

**TCP (`"tcp"`)** - Required for remote dashboard:
- ✅ Remote dashboard capability
- ✅ Dual-stack IPv4/IPv6 support
- ⚠️ Network exposed (use firewall rules)
- **Use when**: Dashboard runs on different server
- **Configuration**: Uses `host_ipv4`, `host_ipv6`, and `port` (unix_socket field ignored) — these are the **dashboard's** reachable address and port
- **IPv6 detection**: Automatic based on address format

**TCP Dual-Stack Configuration:**

HBlink4's event emitter tries `host_ipv6` first and falls back to `host_ipv4`, so list whichever addresses reach the dashboard. Wildcard/bind addresses (`"0.0.0.0"`, `"::"`) belong in the dashboard's `event_receiver` section, never here:

```json
// Dashboard on the same machine
"host_ipv4": "127.0.0.1",
"host_ipv6": "::1",
"port": 8765,

// Dashboard on another machine, dual-stack
"host_ipv4": "192.168.1.100",
"host_ipv6": "2001:db8::100",
"port": 8765,

// Dashboard on another machine, IPv4 only
"disable_ipv6": true,
"host_ipv4": "192.168.1.100",
"port": 8765,
```

### Dashboard Configuration Examples

**Local dashboard (Unix socket - recommended):**
```json
"dashboard": {
    "enabled": true,
    "transport": "unix",
    "unix_socket": "/tmp/hblink4.sock"
}
```

**Local dashboard (TCP - if Unix sockets unavailable):**
```json
"dashboard": {
    "enabled": true,
    "transport": "tcp",
    "host_ipv4": "127.0.0.1",
    "host_ipv6": "::1",
    "port": 8765
}
```

**Remote dashboard (TCP):**
```json
"dashboard": {
    "enabled": true,
    "transport": "tcp",
    "host_ipv4": "192.168.1.100",  // Dashboard server IP
    "host_ipv6": "2001:db8::100",  // Dashboard server IPv6 (optional)
    "port": 8765
}
```

**Disable IPv6 for dashboard only:**
```json
"dashboard": {
    "enabled": true,
    "disable_ipv6": true,  // Use IPv4 only
    "transport": "tcp",
    "host_ipv4": "127.0.0.1",
    "port": 8765
}
```

### Dashboard-Side Configuration

The dashboard listens on two separate things, configured in `dashboard/config.json`.

The `web` section is the **browser-facing web UI**:

```json
"web": {
    "bind": "0.0.0.0",   // All IPv4 interfaces ("::" for dual-stack, "127.0.0.1" for local only)
    "port": 8080
}
```

`python3 run_dashboard.py [bind] [port]` overrides these for one-off runs; with no arguments the config values are used.

The `event_receiver` section is the **listening socket for the link from HBlink4**:

```json
"event_receiver": {
    "transport": "tcp",
    "bind_ipv4": "0.0.0.0",   // Listen on all IPv4 interfaces ("" disables the IPv4 listener)
    "bind_ipv6": "::",        // Listen on all IPv6 interfaces
    "port": 8765,
    "unix_socket": "/tmp/hblink4.sock"
}
```

**Important**: `transport`, `port`, and `unix_socket` must match on both sides. The address fields do **not** mirror each other — the dashboard binds (`bind_*`), HBlink4 dials (`host_*`). A dashboard bound to `0.0.0.0` is reached by an HBlink4 configured with the dashboard's actual IP.

> **Renamed**: these two keys were previously `host_ipv4`/`host_ipv6`, which collided with the identically-named connect-target keys in HBlink4's own `dashboard` section. The old names are still accepted, with a warning logged at startup.

See [Dashboard Documentation](../dashboard/README.md) for the rest of the dashboard-side configuration.

## Connection Type Detection

The `connection_type_detection` section configures how connected devices are categorized and displayed in the dashboard. Connections are grouped into four categories with distinct icons:

- 📶 **Repeaters** - Full duplex repeaters and club sites
- 📱 **Hotspots** - Personal hotspots (Pi-Star, WPSD, MMDVM_HS boards)
- 🔗 **Network Inbound** - Servers connecting to us (HBlink, FreeDMR, BrandMeister)
- ❓ **Other** - Unrecognized connection types

```json
{
    "connection_type_detection": {
        "strict_hotspot_access": true,
        "description": "Categorize connections for dashboard display...",
        "hotspot_packages": [
            "mmdvm_hs", "dvmega", "zumspot", "jumbospot", "nanodv",
            "openspot", "dmo", "simplex"
        ],
        "network_packages": [
            "hblink", "freedmr", "brandmeister", "xlx", "dmr+", "tgif", "ipsc"
        ],
        "repeater_packages": [
            "repeater", "duplex", "stm32", "unknown"
        ],
        "hotspot_software": [
            "pi-star", "pistar", "ps4", "wpsd"
        ],
        "network_software": [
            "hblink", "freedmr", "brandmeister", "xlx"
        ]
    }
}
```

| Setting | Type | Description |
|---------|------|-------------|
| `strict_hotspot_access` | boolean | Require an authenticated, structurally valid RPTC client classified as `hotspot` before it becomes connected; defaults to `true` in BuenoDMR. Only literal `false` disables the policy; missing or invalid values fail closed |
| `hotspot_packages` | array | Package ID substrings that identify hotspots |
| `network_packages` | array | Package ID substrings that identify network links |
| `repeater_packages` | array | Package ID substrings that identify repeaters |
| `hotspot_software` | array | Software ID substrings that identify hotspots (fallback) |
| `network_software` | array | Software ID substrings that identify network links (fallback) |

### Detection Logic

1. **Primary**: Match against `package_id` field from the RPTC config packet
2. **Fallback**: If no package_id match, check `software_id` field
3. **Default**: If neither matches, connection appears in "Other" section

**Matching behavior:**
- All matching is **case-insensitive** (`MMDVM_HS` matches `mmdvm_hs`)
- **Substring matching** is used (`mmdvm_hs` matches `MMDVM_MMDVM_HS_Dual_Hat`)
- Network patterns are checked first, then hotspots, then repeaters
- Generic `MMDVM` (exact match) defaults to repeater

The `hotspot` category is a heuristic over client-supplied RPTC metadata, not
hardware attestation. Strict Hotspot Access applies this category server-side
after allowlist/ESSID and RPTK authentication, before the session becomes
connected. Unknown metadata is denied while strict mode is enabled. Admin can
toggle the flag through the authenticated, CSRF-protected control; the setting
is persisted in this configuration and SQLite metadata, with backup and
rollback around the HBlink4 restart. See [DMR access checks and origin limits](radio-access-security.md)
for protocol fields, observed live values, and limitations.

### Common Package IDs

From real-world connections:

| Package ID | Typical Detection |
|------------|------------------|
| `MMDVM_MMDVM_HS_Hat` | Hotspot (matches `mmdvm_hs`) |
| `MMDVM_MMDVM_HS_Dual_Hat` | Hotspot (matches `mmdvm_hs`) |
| `MMDVM_DMO` | Hotspot (matches `dmo`) |
| `MMDVM_HBlink` | Network (matches `hblink`) |
| `MMDVM` | Repeater (exact match default) |
| `MMDVM_Unknown` | Repeater (matches `unknown`) |

### Customizing Detection

To add custom hardware to a category, add its package_id substring to the appropriate array:

```json
// Example: Add custom hardware to hotspots
"hotspot_packages": [
    "mmdvm_hs", "dvmega", "zumspot", "jumbospot", "nanodv",
    "openspot", "dmo", "simplex",
    "my_custom_hotspot"  // Added
]
```

### Stream Management

The `stream_timeout` and `stream_hang_time` settings control two different aspects of DMR transmission management:

- **`stream_timeout`**: Fallback cleanup timeout (default: 2.0 seconds). This is used **only** when a DMR terminator frame is lost or not received. Under normal operation, streams end immediately when a terminator frame is detected (~60ms). This timeout ensures slot cleanup even if the terminator packet is dropped. **Recommended: 2.0 seconds** to handle worst-case packet loss scenarios.
  
- **`stream_hang_time`**: Slot reservation period (default: 10.0-20.0 seconds). After a stream ends (either via terminator frame or timeout), the timeslot remains reserved for the same RF source for this duration, preventing other stations from hijacking the slot between transmissions in a conversation. **Recommended: 10.0-20.0 seconds** depending on operator speed and network usage patterns.

**How It Works:**
1. DMR transmission begins → stream active
2. DMR terminator frame received → stream ends immediately (~60ms), hang time begins
3. If terminator lost → stream_timeout (2s) triggers cleanup, hang time begins  
4. During hang time → only original source can re-use the slot
5. After hang time expires → slot available to all

**DMR Timing Notes:**
- DMR voice packets transmitted approximately every 60ms
- DMR terminator frame signals end of transmission (primary detection method)
- stream_timeout is a fallback safety mechanism only
- hang_time prevents slot hijacking during multi-transmission conversations

See [Hang Time Documentation](hang_time.md) for detailed explanation of these features.

## Dynamic Talkgroup Participation

The optional `dynamic_talkgroups` section lets one talkgroup have local
subscribe/disconnect behavior without making the disconnect TG routable.

This is disabled by default. When enabled, a group voice PTT on the configured
`slot`/`talkgroup` joins or reactivates only that connected repeater/hotspot for
receiving that talkgroup. A group voice PTT on `disconnect_talkgroup` leaves
locally: it is not emitted as a normal stream, is not forwarded to any other
repeater/hotspot, and only disables receive participation for the sender.
The HomeBrew connection remains authenticated and connected.

```json
"dynamic_talkgroups": {
    "enabled": true,
    "slot": 2,
    "talkgroup": 100,
    "disconnect_talkgroup": 4100,
    "initial_active": true
}
```

Options:

| Option | Type | Description |
|--------|------|-------------|
| `enabled` | boolean | Enables dynamic participation. Default: `false` |
| `slot` | number | Timeslot controlled by this feature. BuenoDMR uses `2` |
| `talkgroup` | number | Talkgroup that activates/reactivates receive participation |
| `disconnect_talkgroup` | number | Local command TG that deactivates receive participation and is never forwarded |
| `initial_active` | boolean | Whether a newly connected repeater/hotspot starts active. Default: `true` |

For a BuenoDMR TS2/TG100-only network, keep the normal repeater ACL restrictive:

```json
"repeater_configurations": {
    "default": {
        "passphrase": "CHANGE-ME",
        "slot1_talkgroups": [],
        "slot2_talkgroups": [100],
        "trust": false
    }
}
```

Do not add TG4100 to `slot2_talkgroups`. TG4100 is handled before normal stream
routing only when `dynamic_talkgroups.enabled=true`; otherwise it remains denied
by the ordinary TG100-only ACL.

For WPSD Custom DMR Network, expose both TGs to HBlink4:

```ini
TGRewrite0=2,100,2,100,1
TGRewrite1=2,4100,2,4100,1
```

## Blacklist Rules

The `blacklist` section defines patterns for blocking unwanted repeaters. Each pattern can match by ID, ID range, or callsign.

```json
{
    "blacklist": {
        "patterns": [
            {
                "name": "Pattern Name",
                "description": "Pattern Description",
                "match": {
                    "ids": [123456, 123457]
                    // OR "id_ranges": [[100000, 199999]]
                    // OR "callsigns": ["BADACTOR*"]
                },
                "reason": "Reason for blocking"
            }
        ]
    }
}
```

### Blacklist Pattern Options

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique name for the blacklist pattern |
| `description` | string | Detailed description of the pattern |
| `match` | object | One of three match types (see below) |
| `reason` | string | Reason shown when blocking a repeater |

### Match Types

Patterns support three match types (one or more per pattern):
- **Specific IDs**: `"ids"` - Array of DMR IDs
- **ID Ranges**: `"id_ranges"` - Array of [start, end] ranges (inclusive)
- **Callsign Patterns**: `"callsigns"` - Array of patterns with "*" wildcards

Multiple match types in a single pattern are combined with OR logic (any match triggers the rule).

**Examples:**
```json
// Single match type
{
    "name": "Blocked Range",
    "description": "Unauthorized range",
    "match": {
        "id_ranges": [[1000, 1999]]
    },
    "reason": "Unauthorized network"
}

// Multiple ID ranges
{
    "name": "Blocked Multiple Ranges",
    "description": "Multiple unauthorized ranges",
    "match": {
        "id_ranges": [[1000, 1999], [5000, 5999], [9000, 9999]]
    },
    "reason": "Unauthorized network ranges"
}

// Multiple match types (IDs + ranges + callsigns)
{
    "name": "Blocked Combined",
    "description": "Specific IDs, ranges, and callsigns",
    "match": {
        "ids": [123456],
        "id_ranges": [[1000, 1999]],
        "callsigns": ["BADACTOR*"]
    },
    "reason": "Network abuse"
}
```

## Repeater Configurations

The `repeater_configurations` section defines patterns for matching repeaters and their configurations. It includes an optional default configuration and specific patterns.

```json
{
    "repeater_configurations": {
        "patterns": [...],
        "default": {
            "passphrase": "default-key",
            "slot1_talkgroups": [1],
            "slot2_talkgroups": [2]
        }
    }
}
```

**Note:** The `default` configuration is **optional**. If omitted, repeaters that don't match any pattern will be rejected during authentication. This provides better security by requiring explicit configuration for all connecting repeaters.

### Pattern Structure

Each pattern defines a match rule and associated configuration:

```json
{
    "name": "Pattern Name",
    "description": "Optional description for documentation",
    "match": {
        "ids": [312100, 312101],
        "id_ranges": [[312000, 312099]],
        "callsigns": ["WA0EDA*"]
    },
    "config": {
        "passphrase": "secret-key",
        "slot1_talkgroups": [8, 9],
        "slot2_talkgroups": [3100, 3101]
    }
}
```

**Note**: The `description` field is optional and for human documentation only—it is not used by the program.

### Match Types

Repeater patterns support three match types (one or more per pattern):
- **Specific IDs**: `"ids"` - Array of DMR IDs
- **ID Ranges**: `"id_ranges"` - Array of [start, end] ranges (inclusive)
- **Callsign Patterns**: `"callsigns"` - Array of patterns with "*" wildcards

Multiple match types in a single pattern are combined with OR logic (any match triggers the rule).

**Match-All Pattern**: Use `"callsigns": ["*"]` to match any repeater (useful for catch-all patterns).

**Examples:**
```json
// Single ID range
{
    "name": "KS-DMR Range",
    "match": {
        "id_ranges": [[312000, 312099]]
    },
    "config": {
        "passphrase": "ks-dmr-key",
        "slot1_talkgroups": [8, 9],
        "slot2_talkgroups": [3120]
    }
}

// Multiple ID ranges
{
    "name": "Regional Network",
    "description": "Regions 310, 311, and 312",
    "match": {
        "id_ranges": [[310000, 310999], [311000, 311999], [312000, 312999]]
    },
    "config": {
        "passphrase": "regional-key",
        "slot1_talkgroups": [1, 2, 3],
        "slot2_talkgroups": [3100, 3110, 3120]
    }
}

// Multiple match types combined
{
    "name": "KS-DMR Network",
    "description": "All KS-DMR repeaters",
    "match": {
        "ids": [315035, 3129054],
        "id_ranges": [[312001, 312099]],
        "callsigns": ["WA0EDA*"]
    },
    "config": {
        "passphrase": "network-key",
        "slot1_talkgroups": [2, 9],
        "slot2_talkgroups": [3120]
    }
}

// Match-all pattern (catch-all for any repeater not matched above)
{
    "name": "Guest Repeaters",
    "match": {
        "callsigns": ["*"]
    },
    "config": {
        "passphrase": "guest-key",
        "slot1_talkgroups": [8],
        "slot2_talkgroups": [3100]
    }
}
```

### Configuration Options

| Option | Type | Description |
|--------|------|-------------|
| `passphrase` | string | Authentication key for the repeater (required) |
| `slot1_talkgroups` | array | List of allowed talkgroup IDs for timeslot 1 (bidirectional) |
| `slot2_talkgroups` | array | List of allowed talkgroup IDs for timeslot 2 (bidirectional) |
| `trust` | boolean | If true, repeater can use any TG (config TGs become defaults) |
| `default_unit_calls` | boolean | Default unit (private) call participation for repeaters matching this pattern. Repeaters can override via `UNIT=true\|false` in RPTO. (default: `false`) |

**Symmetric Routing:**
The same talkgroup lists control BOTH directions:
- **FROM repeater (inbound)**: Only listed TGIDs are accepted from the repeater
- **TO repeater (outbound)**: Only listed TGIDs are forwarded to the repeater

**Repeater OPTIONS Packet:**
Repeaters can optionally send an OPTIONS packet (RPTO in HomeBrew Protocol) to request specific talkgroups. The server's behavior depends on whether this packet is sent:

| Repeater Behavior | Server Response |
|-------------------|-----------------|
| **No OPTIONS sent** | Uses talkgroups from HBlink4 server configuration (`slot1_talkgroups`, `slot2_talkgroups`) |
| **OPTIONS sent (e.g., "TS1=1,2;TS2=3100")** | Uses intersection of requested TGs and server-configured TGs (cannot expand beyond server config) |
| **OPTIONS sent but empty (no TS specified)** | Uses talkgroups from HBlink4 server configuration |
| **Trusted repeater (trust: true)** | Can request any TGs via OPTIONS; server config becomes defaults if no OPTIONS sent |

**Talkgroup Filtering Modes:**

| Configuration | Behavior | Use Case |
|---------------|----------|----------|
| **Missing/Not configured** | Allow ALL talkgroups | Legacy/unrestricted repeaters |
| **Empty list `[]`** | **DENY ALL** talkgroups | Disable a timeslot completely |
| **List with TGs `[1,2,3]`** | Allow ONLY listed TGs | Normal operation with specific TGs |

⚠️ **IMPORTANT**: An empty list `[]` means "deny all" - no traffic will be accepted or forwarded on that timeslot!

**Examples:**

```json
// Example 1: Allow specific talkgroups
"config": {
    "passphrase": "my-secret-key",
    "slot1_talkgroups": [2, 9],       // Accept/forward ONLY TG 2 and 9 on TS1
    "slot2_talkgroups": [3120, 3121]  // Accept/forward ONLY 3120 and 3121 on TS2
}

// Example 2: Disable a timeslot (deny all)
"config": {
    "passphrase": "my-secret-key",
    "slot1_talkgroups": [],      // DENY ALL traffic on TS1 (slot disabled)
    "slot2_talkgroups": [3120]   // Accept/forward ONLY TG 3120 on TS2
}

// Example 3: No configuration = allow all (backward compatibility)
// If patterns section is omitted or pattern doesn't match,
// the default config applies. If default has no TG lists defined,
// all traffic is allowed (legacy behavior).
"default": {
    "passphrase": "default-password"
    // No slot1_talkgroups or slot2_talkgroups = allow all TGs
}
```

### Trusted Repeaters

The `trust` flag allows designated repeaters to bypass talkgroup restrictions. This is useful for core network repeaters managed by trusted operators.

**Normal Behavior (trust: false or not set):**
- If repeater sends OPTIONS packet: Server uses **intersection** of (requested TGs) ∩ (server-configured TGs)
- If repeater does NOT send OPTIONS: Server uses the TGs from this HBlink4 server configuration
- Repeater limited to only server-configured TGs

**Trusted Behavior (trust: true):**
- If repeater sends OPTIONS packet: Server uses requested TGs **as-is** (no intersection)
- If repeater does NOT send OPTIONS: Server uses the TGs from this HBlink4 server configuration as defaults
- Trusted repeaters can dynamically access any TG without server reconfiguration

**Example:**
```json
{
    "name": "Core Network Repeaters",
    "description": "Trusted repeaters managed by core team",
    "match": {
        "ids": [312000, 312001]
    },
    "config": {
        "passphrase": "core-secret-key",
        "trust": true,
        "slot1_talkgroups": [8, 9],      // Used if repeater doesn't send OPTIONS
        "slot2_talkgroups": [3120, 3121] // Used if repeater doesn't send OPTIONS
    }
}
```

**Use Cases:**
- **Core network repeaters** - Full access for network management
- **Testing/development** - Trusted test repeaters can access any TG
- **Administrative repeaters** - Network monitoring and troubleshooting

**Security Note:** Only assign `trust: true` to repeaters under your direct control. Trusted repeaters have unrestricted access to all talkgroups on the network.

### DMRD Translation (RPTO Extensions)

Trusted repeaters can declare **slot / talkgroup translation** and an optional **outbound rf_src override** via their RPTO (OPTIONS) packet. This lets a repeater's local addressing (what the radio user sees) differ from the network addressing (what the rest of the HBlink4 network sees).

See **[dmrd_translation.md](dmrd_translation.md)** for full semantics, use cases, and operational notes. Quick summary below.

**Extended RPTO grammar:**

```
TS1  = entry[,entry...]    ; subscription (and optional remap) on network TS1
TS2  = entry[,entry...]    ; subscription (and optional remap) on network TS2
SRC  = radio_id            ; outbound rf_src override (group voice only)
UNIT = true|false          ; opt in/out of unit (private) call routing

entry  = net_tgid_spec [ : local_slot [ : local_tgid ] ]
net_tgid_spec = N         ; exact TGID (specificity 3)
              | N-M       ; inclusive range, expanded at parse time (specificity 2)
local_slot = 1 | 2 | *    ; * = preserve the network slot
local_tgid = N | *        ; * = preserve the matched network TGID
```

**`UNIT=`** overrides the pattern's `default_unit_calls` for this repeater. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off` (case-insensitive). **Honored only for trusted repeaters (`trust: true`)** — untrusted repeaters silently stay on the pattern default with a warning logged. If absent from RPTO, the pattern default is used.

Rules:

- **Translation syntax is honored only when `trust: true`**. Non-trusted repeaters keep the legacy TG-subscription behavior; any remap is silently ignored with a warning.
- An entry with **no colon** is a pure subscription (no translation), same as today.
- Ranges (`N-M`) are expanded to individual map entries at parse time. Max 10,000 tgids per range.
- **Wildcards are not supported on the network side** (no `*`, no `N*` prefix). Use a specific TGID or a range.
- **Most-specific wins on collision**: exact (specificity 3) beats range (specificity 2). If two rules claim the same local or network key, the less-specific one is dropped with a warning.
- **`SRC=` applies to group voice only**. Every outgoing packet from this repeater has its rf_src rewritten to this ID — one-way, no reverse translation needed (group destinations have no return address). Use when you want the rest of the network to see all traffic from a repeater as a single "site radio".

**Processing order (important):** translation is the **first** thing that runs on ingress and the **last** thing that runs on egress. ACL checks, hang time, contention detection, and routing all operate in **network vocabulary** — the translation layer normalizes the packet before those checks, and rewrites it back to the target's local vocabulary only as the final step before sending. When you configure `slot1_talkgroups` / `slot2_talkgroups` (or any rule that references a TS/TGID), think in network vocabulary. See [dmrd_translation.md § Processing order](dmrd_translation.md#processing-order) for the full step list.

**Quick examples:**

```
Options = "TS1=9"                          ; legacy: subscribe to net TS1/TG9
Options = "TS1=9:2:9"                      ; net TS1/TG9 ↔ local TS2/TG9 (slot swap)
Options = "TS1=9:2:32"                     ; net TS1/TG9 ↔ local TS2/TG32
Options = "TS1=3000-3200:2:*"              ; range on TS1 delivered on local TS2, tgid preserved
Options = "TS1=3000-3200:2:*,3120:1:3120"  ; same range EXCEPT TG3120 stays on local TS1
Options = "TS1=9:2:32;SRC=9990001"         ; translation + outbound rf_src override
```

When an RPTO with translation arrives during an active stream, the new rules are applied immediately (a warning is logged); the in-flight stream will finish on its old rules within a few seconds.

### Pattern Matching Priority

Patterns are evaluated in the order they appear in the configuration file. The first pattern that matches is used. Within each pattern, all match types (IDs, ID ranges, callsigns) are checked with OR logic.

## Network Outbound

The `outbound_connections` section is **optional** and defines server-to-server links. This allows your HBlink4 server to connect to other HomeBrew Protocol servers as a client (similar to how repeaters connect to your server).

```json
{
    "outbound_connections": [
        {
            "enabled": true,
            "name": "Regional-Master-Server",
            "address": "master.example.com",
            "port": 62031,
            "password": "remote-server-password",
            "radio_id": 312999,
            "callsign": "K0USY-L",
            "rx_frequency": 449000000,
            "tx_frequency": 444000000,
            "power": 50,
            "colorcode": 1,
            "latitude": 38.0,
            "longitude": -97.0,
            "height": 100,
            "location": "Network Link",
            "description": "Link to regional master",
            "url": "https://hblink.example.com",
            "software_id": "HBlink4",
            "package_id": "HBlink4 v2.0",
            "options": "TS1=1,2,3,8,9;TS2=3100,3120,3121,9998"
        }
    ]
}
```

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | boolean | Enable/disable this connection without removing it |
| `name` | string | Unique name for this connection (used in logs) |
| `address` | string | Hostname or IP address of remote server |
| `port` | number | UDP port of remote server (typically 62031) |
| `password` | string | Authentication password for remote server |
| `radio_id` | number | DMR ID to use when connecting (must be unique) |

### Optional Metadata Fields

These fields are sent to the remote server during the RPTC (configuration) handshake. If not specified, defaults are used:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `callsign` | string | `""` | Callsign identifier |
| `rx_frequency` | number | `0` | RX frequency in Hz |
| `tx_frequency` | number | `0` | TX frequency in Hz |
| `power` | number | `0` | Power in watts |
| `colorcode` | number | `1` | DMR color code |
| `latitude` | number | `0.0` | Latitude coordinate |
| `longitude` | number | `0.0` | Longitude coordinate |
| `height` | number | `0` | Height in meters |
| `location` | string | `""` | Location description |
| `description` | string | `""` | Connection description |
| `url` | string | `""` | Website URL |
| `software_id` | string | `"HBlink4"` | Software identifier |
| `package_id` | string | `"HBlink4 v2.0"` | Package version |
| `unit_calls_enabled` | boolean | `false` | Whether unit (private) calls traverse this outbound link. When `true`, local unit calls fan out over this link and unit calls arriving on it are forwarded to local repeaters. When `false`, unit calls are dropped at the link boundary. Set to `true` only for peers that participate in unit-call routing (e.g. other HBlink4 servers). |

### Unit (Private) Call Forwarding

When `unit_calls_enabled: true`, this outbound participates in unit-call routing:

- **Local → outbound**: if the target subscriber isn't in our user cache (or its cache entry points to a different location), a local unit call is broadcast to every unit-enabled outbound and local repeater. Once we've heard the target (over this link or another), subsequent calls route one-to-one to wherever the cache says it was last seen.
- **Outbound → local**: unit calls arriving over the link are forwarded to local repeaters using the same cache/broadcast logic, and the source subscriber is cached as reachable via this outbound. A subsequent local call to that subscriber will route back over the same link — an implicit reverse-path forwarding tree when both peers run HBlink4.
- **Anti-loop**: unit calls arriving on an outbound are never re-forwarded to any outbound (including a different one). Each peer owns fanout on its own side of the link.

> ℹ️ **No cross-outbound forwarding today (either call type).** Group calls behave the same way — traffic arriving on an outbound link is delivered to local repeaters only, never to another outbound. For star/tree topologies (the common case) this is correct and loop-safe. For chain or mesh topologies involving three or more HBlink4 servers, traffic will not transit through a middle peer. A future enhancement using stream-ID-based loop detection is tracked in [TODO.md](TODO.md).

### Talkgroup Filtering (OPTIONS)

The `options` field controls which talkgroups are accepted/forwarded on this outbound connection. It uses the same format as the HomeBrew Protocol RPTO (options) packet:

**Format:** `"TS1=tg1,tg2,tg3;TS2=tg4,tg5,tg6"`

**Special values:**
- `*` - Accept all talkgroups on this timeslot
- Empty (no TGs) - Deny all on this timeslot
- Omit options field entirely - Accept all on both timeslots

**Examples:**

```json
// Specific talkgroups on each slot
"options": "TS1=1,2,3,8,9;TS2=3100,3120,3121,9998"

// All talkgroups on both slots
"options": "TS1=*;TS2=*"

// Only TS2 active, TS1 disabled
"options": "TS1=;TS2=3100,3120"

// Only TS1 active, TS2 disabled
"options": "TS1=1,2,3;TS2="

// No options field = accept all (backward compatibility)
// (omit the "options" field entirely)
```

### Bidirectional Traffic

Outbound connections are **bidirectional**:
- **Outbound (local → remote)**: DMR traffic from your local repeaters matching the TG filters is forwarded to the remote server
- **Inbound (remote → local)**: DMR traffic from the remote server matching the TG filters is forwarded to your local repeaters

The same talkgroup filters apply in both directions.

### Connection Behavior

- **Automatic reconnection**: If connection is lost, HBlink4 automatically attempts to reconnect
- **DNS resolution**: The `address` field supports both hostnames (resolved via DNS) and IP addresses
- **Protocol state machine**: Full HomeBrew Protocol handshake (RPTL → MSTCL → RPTK → RPTACK → RPTC → RPTACK → RPTO → RPTACK)
- **Keepalive**: Regular RPTPING/MSTPONG exchanges maintain the connection
- **TDMA slot tracking**: Each outbound connection tracks slot usage independently (respects TDMA constraints)

### Multiple Connections

You can define multiple outbound connections to link with several servers:

```json
"outbound_connections": [
    {
        "enabled": true,
        "name": "Primary-Server",
        "address": "primary.example.com",
        "port": 62031,
        "password": "password1",
        "radio_id": 312999,
        "options": "TS1=1,2,3;TS2=3100,3120"
    },
    {
        "enabled": true,
        "name": "Secondary-Server",
        "address": "secondary.example.com",
        "port": 62031,
        "password": "password2",
        "radio_id": 312998,
        "options": "TS1=8,9;TS2=3121,3122"
    },
    {
        "enabled": false,
        "name": "Backup-Server",
        "address": "backup.example.com",
        "port": 62031,
        "password": "password3",
        "radio_id": 312997,
        "options": "TS1=*;TS2=*"
    }
]
```

**Important**: Each connection must use a **unique radio_id** to avoid conflicts.

### Disabling Connections

Set `enabled: false` to temporarily disable a connection without removing it from the HBlink4 server configuration:

```json
{
    "enabled": false,
    "name": "Disabled-Connection",
    "address": "example.com",
    "port": 62031,
    "password": "password",
    "radio_id": 312996
}
```

## OpenBridge Trunks

OpenBridge (OBP) trunks carry many talkgroups' worth of server-to-server traffic
over a single HMAC-authenticated socket — typically to/from a core such as
HBlink3. Unlike Network Outbound (which emulates a two-slot repeater), OBP is
stream-multiplexed and TGID-transparent. Declare trunks in the top-level
`openbridge_connections` array. Full guide: [OpenBridge Trunks](openbridge.md).

```json
"openbridge_connections": [
    {
        "enabled": true,
        "name": "KS-Core-Trunk",
        "network_id": 3129900,
        "local_address": "0.0.0.0",
        "local_port": 62035,
        "target_address": "core.example.net",
        "target_port": 62035,
        "passphrase": "shared-obp-secret",
        "preserve_source_peer": true,
        "talkgroup_slots": {
            "31": "1",
            "3120": "2"
        }
    }
]
```

### Fields

| Field | Required | Meaning |
|-------|----------|---------|
| `enabled` | yes | Start this trunk. |
| `name` | yes | Unique trunk name (logs + dashboard). |
| `network_id` | yes | This side's OBP network ID. Stamped into egress RptrId **only** when `preserve_source_peer` is `false`. |
| `local_address` / `local_port` | yes | Local bind for this trunk's own socket (one socket per OBP). |
| `target_address` / `target_port` | yes | Remote OBP peer; ingress is accepted only from this socket. |
| `passphrase` | yes | Shared secret keying the per-packet HMAC-SHA1. Must match the peer. |
| `preserve_source_peer` | no (default `true`) | `true`: keep the true source peer in the egress RptrId; `false`: overwrite with `network_id` (Brandmeister-spec). |
| `talkgroup_slots` | yes | Canonical **TGID → local TS** map (ownership + fail-closed filter + slot assignment). |

### `talkgroup_slots`

Both the TGID key and the TS value are JSON strings (`"31": "1"`) so the file
reads consistently; they are parsed to a 3-byte TGID and an integer TS at load.
The map form structurally enforces **one TS per TGID**. Any TGID not listed is
**dropped** in both directions (fail-closed). The wire TGID is used as-is — an
OBP edge assigns a timeslot but never renumbers the talkgroup.

### Validation (fatal at startup)

- TS must be `1` or `2`; TGID must be in range.
- **One OBP per canonical TGID** across all *enabled* trunks (prevents OBP→OBP
  transit and duplication). A disabled standby trunk may mirror an active
  trunk's TGIDs, so flipping `enabled` performs a manual failover.

## Example Configuration

Two sample files ship with the repository:

- **`config/config_sample.json`** — a minimal, working starting point. Copy this
  to `config/config.json` and change the passphrase; everything else has a
  usable default.
- **`config/config_advanced_sample.json`** — a reference showing every
  configuration section with example values. Paste the sections you need into
  your own config. Do not use it as your configuration directly: its blacklist
  and repeater patterns match invented ID ranges that may overlap your own
  repeaters, and its link and trunk examples point at hosts that do not exist.

````

## Example Configuration

See the `config/hblink.json` file in the repository for a complete example configuration with multiple patterns and talkgroups.
