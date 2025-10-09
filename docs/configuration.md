# HBlink4 Configuration Guide

HBlink4 uses a JSON configuration file to define server settings, repeater access control rules, and talkgroup definitions. This guide explains each configuration section and its options.

## Configuration File Structure

The configuration file consists of five main sections:
- **Global Settings** - Server-wide settings
- **Dashboard** - Web dashboard and event communication
- **Blacklist Rules** - Access control for blocking repeaters
- **Repeater Configurations** - Per-repeater authentication and routing
- **Talkgroup Definitions** - Talkgroup names and bridging

## Global Settings

The `global` section contains server-wide settings that control the basic operation of HBlink4.

```json
{
    "global": {
        "max_missed": 3,
        "timeout_duration": 30,
        "disable_ipv6": false,
        "bind_ipv4": "0.0.0.0",
        "bind_ipv6": "::",
        "port_ipv4": 62031,
        "port_ipv6": 62031,
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
| `disable_ipv6` | boolean | **Disable IPv6 globally** - use only if your network has broken IPv6 routing (default: false) |
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

**Note on IPv6**: HBlink4 is dual-stack native and will bind to both IPv4 and IPv6 by default. If your network appears to support IPv6 but connections don't establish properly (a common issue with misconfigured IPv6), set `disable_ipv6: true` to force IPv4-only mode.

**User Cache**: The user cache tracks the last known repeater for each DMR ID to enable efficient private call routing. Entries are automatically cleaned up every 60 seconds. The timeout must be at least 60 seconds.

### Dual-Stack IPv6 Support

HBlink4 is **dual-stack native** and can listen on both IPv4 and IPv6 simultaneously:

- Set `bind_ipv4` to `"0.0.0.0"` to listen on all IPv4 interfaces
- Set `bind_ipv6` to `"::"` to listen on all IPv6 interfaces
- Both can be active simultaneously for maximum compatibility
- Specific addresses can be used instead of wildcards (e.g., `"192.168.1.10"` or `"2001:db8::1"`)
- Use `disable_ipv6: true` to force IPv4-only mode if IPv6 is broken on your network

**Common Issue: "Address Already in Use" on IPv6 Bind**

If you see an error like "address already in use" when binding IPv6 with the same port as IPv4, your system's IPv6 stack is in dual-stack mode (IPv6 can handle both IPv4 and IPv6 on the same port). This is **normal and expected** on many Linux systems.

**Solutions:**
1. **Use different ports** (simple): `port_ipv4: 62031`, `port_ipv6: 62032`
2. **Disable IPv6** (IPv4-only): Set `disable_ipv6: true`
3. **Let IPv6 handle both** (advanced): Set `bind_ipv4: ""` to disable IPv4 bind

**Example configurations:**
```json
// Dual-stack with separate ports (RECOMMENDED if you see bind errors)
"bind_ipv4": "0.0.0.0",
"bind_ipv6": "::",
"port_ipv4": 62031,
"port_ipv6": 62032,

// IPv4 only (simple and reliable)
"disable_ipv6": true,
"bind_ipv4": "0.0.0.0",
"port_ipv4": 62031,

// Specific addresses (no port conflict)
"bind_ipv4": "192.168.1.10",
"bind_ipv6": "2001:db8::1",
"port_ipv4": 62031,
"port_ipv6": 62031
```

## Dashboard Configuration

The `dashboard` section is a **top-level** configuration (not nested under `global`) and controls the real-time monitoring dashboard and event communication:

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
| `host_ipv4` | string | IPv4 address for TCP transport (e.g., "127.0.0.1") |
| `host_ipv6` | string | IPv6 address for TCP transport (e.g., "::1") |
| `port` | number | Port number for TCP transport (default: 8765) |
| `unix_socket` | string | Unix socket path for Unix transport (default: "/tmp/hblink4.sock") |
| `buffer_size` | number | Socket send buffer size (default: 65536) |

### Transport Options

**Unix Socket (`"unix"`)** - Recommended for local dashboard:
- ✅ Fastest performance (~0.5-1μs per event)
- ✅ Same-host only (most secure)
- ✅ Automatic cleanup on startup
- ✅ File permissions control access
- **Use when**: Dashboard runs on same server as HBlink4
- **Configuration**: Only `unix_socket` path is used (host and port fields ignored)

**TCP (`"tcp"`)** - Required for remote dashboard:
- ✅ Remote dashboard capability
- ✅ Dual-stack IPv4/IPv6 support
- ⚠️ Network exposed (use firewall rules)
- **Use when**: Dashboard runs on different server
- **Configuration**: Uses `host_ipv4`, `host_ipv6`, and `port` (unix_socket field ignored)
- **IPv6 detection**: Automatic based on address format

**TCP Dual-Stack Configuration:**

When using TCP transport with HBlink4 and dashboard on **different machines**, you have the same dual-stack options as the main UDP server:

```json
// Localhost (both on same machine) - NO dual-stack issues
"host_ipv4": "127.0.0.1",
"host_ipv6": "::1",
"port": 8765,

// Remote, dual-stack mode (RECOMMENDED for remote dashboard)
"host_ipv4": "",              // Empty = disable IPv4 listener
"host_ipv6": "::",            // Listen on all IPv6 interfaces
"port": 8765,                 // Single port handles both IPv4 and IPv6

// Remote, separate ports (if dual-stack conflicts)
"host_ipv4": "0.0.0.0",
"host_ipv6": "::",
"port": 8765,                 // Note: May need different ports if bind error

// Remote, IPv4-only (simplest)
"disable_ipv6": true,
"host_ipv4": "0.0.0.0",
"port": 8765,
```

**Note**: HBlink4's event emitter tries IPv6 first, then falls back to IPv4 automatically, so dual-stack configuration on the dashboard side works seamlessly.

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

**Important**: Both HBlink4 config (`config/config.json`) and dashboard config (`dashboard/config.json`) must use the **same transport type and connection details**. See [Dashboard Documentation](../dashboard/README.md) for dashboard-side configuration.

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

The `repeater_configurations` section defines patterns for matching repeaters and their configurations. It includes a default configuration and specific patterns.

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

**Symmetric Routing:**
The same talkgroup lists control BOTH directions:
- **FROM repeater (inbound)**: Only listed TGIDs are accepted from the repeater
- **TO repeater (outbound)**: Only listed TGIDs are forwarded to the repeater

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

### Pattern Matching Priority

Patterns are evaluated in the order they appear in the configuration file. The first pattern that matches is used. Within each pattern, all match types (IDs, ID ranges, callsigns) are checked with OR logic.

## Talkgroup Definitions

The `talkgroups` section defines available talkgroups and their properties.

```json
{
    "talkgroups": {
        "3100": {
            "name": "Local",
            "bridge": true
        }
    }
}
```

### Talkgroup Options

| Option | Type | Description |
|--------|------|-------------|
| `name` | string | Human-readable name for the talkgroup |
| `bridge` | boolean | Whether to bridge this talkgroup between repeaters |

## Example Configuration

See the `config/hblink.json` file in the repository for a complete example configuration with multiple patterns and talkgroups.
