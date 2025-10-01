# HBlink4 Configuration Guide

HBlink4 uses a JSON configuration file to define server settings, repeater access control rules, and talkgroup definitions. This guide explains each configuration section and its options.

## Configuration File Structure

The configuration file consists of four main sections:
- Global Settings
- Blacklist Rules
- Repeater Configurations
- Talkgroup Definitions

## Global Settings

The `global` section contains server-wide settings that control the basic operation of HBlink4.

```json
{
    "global": {
        "path": "./",
        "ping_time": 5,
        "max_missed": 3,
        "use_ipv6": false,
        "bind_ip": "0.0.0.0",
        "bind_port": 62031,
        "logging": {
            "file": "logs/hblink.log",
            "console_level": "INFO",
            "file_level": "DEBUG",
            "log_protocol": false,
            "log_dmr_data": false,
            "log_status_updates": true
        },
        "stats_interval": 60,
        "report_stats": true
    }
}
```

| Setting | Type | Description |
|---------|------|-------------|
| `path` | string | Base path for relative file references |
| `ping_time` | number | Seconds between keep-alive pings to repeaters |
| `max_missed` | number | Maximum missed pings before disconnecting a repeater |
| `use_ipv6` | boolean | Enable IPv6 support (default: false) |
| `bind_ip` | string | IP address to bind the server to ("0.0.0.0" for all interfaces) |
| `bind_port` | number | UDP port for the server (default: 62031) |
| `logging.file` | string | Path to log file |
| `logging.console_level` | string | Logging level for console output ("DEBUG", "INFO", "WARNING", "ERROR") |
| `logging.file_level` | string | Logging level for file output ("DEBUG", "INFO", "WARNING", "ERROR") |
| `logging.log_protocol` | boolean | Log detailed protocol messages (default: false) |
| `logging.log_dmr_data` | boolean | Log DMR data packets (default: false) |
| `logging.log_status_updates` | boolean | Log repeater status updates (default: true) |
| `stats_interval` | number | Seconds between statistics reports |
| `report_stats` | boolean | Enable statistics reporting |
| `stream_timeout` | float | Seconds without packets before stream is considered ended (default: 2.0) |
| `stream_hang_time` | float | Seconds to reserve slot for same source after stream ends (default: 3.0) |

The logging system supports different levels for console and file output. This allows you to have detailed debugging information in your log files while keeping the console output cleaner. The protocol, DMR data, and status update flags let you control what types of messages are logged:

- `log_protocol`: When enabled, logs all protocol messages (RPTL, RPTK, RPTC, etc.)
- `log_dmr_data`: When enabled, logs DMR data packets (usually high volume)
- `log_status_updates`: When enabled, logs repeater status updates like RSSI

### Stream Management

The `stream_timeout` and `stream_hang_time` settings control two different aspects of DMR transmission management:

- `stream_timeout`: **Fallback cleanup timeout** (default: 2.0 seconds). This is used when a DMR terminator frame is lost or not received. Under normal operation, streams end immediately when a terminator frame is detected. This timeout ensures slot cleanup even if the terminator packet is dropped. **Recommended: 2.0 seconds** to handle worst-case packet loss scenarios.
  
- `stream_hang_time`: **Slot reservation period** (default: 10.0-20.0 seconds). After a stream ends (either via terminator frame or timeout), the timeslot remains reserved for the same RF source for this duration, preventing other stations from hijacking the slot between transmissions in a conversation. **Recommended: 10.0-20.0 seconds** depending on operator speed and network usage patterns.

**How It Works:**
1. DMR transmission begins → stream active
2. DMR terminator frame received → stream ends immediately, hang time begins
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

Only ONE match type can be used per pattern:

1. **Specific IDs**:
   ```json
   "match": {
       "ids": [310666, 310667]
   }
   ```

2. **ID Ranges**:
   ```json
   "match": {
       "id_ranges": [[315000, 315999]]
   }
   ```

3. **Callsign Patterns**:
   ```json
   "match": {
       "callsigns": ["BADACTOR*", "SPAM*"]
   }
   ```
   Note: Callsign patterns support "*" as a wildcard.

## Repeater Configurations

The `repeater_configurations` section defines patterns for matching repeaters and their configurations. It includes a default configuration and specific patterns.

```json
{
    "repeater_configurations": {
        "patterns": [...],
        "default": {
            "enabled": true,
            "timeout": 30,
            "passphrase": "default-key",
            "talkgroups": [3100],
            "description": "Default Configuration"
        }
    }
}
```

### Pattern Structure

Each pattern defines a match rule and associated configuration:

```json
{
    "name": "Pattern Name",
    "description": "Pattern Description",
    "match": {
        "ids": [312100, 312101]
        // OR "id_ranges": [[312000, 312099]]
        // OR "callsigns": ["WA0EDA*"]
    },
    "config": {
        "enabled": true,
        "timeout": 30,
        "passphrase": "secret-key",
        "talkgroups": [3100, 3101],
        "description": "Repeater Configuration"
    }
}
```

### Match Types

Like blacklist patterns, repeater patterns support three match types:
- Specific IDs: `ids`: Array of DMR IDs
- ID Ranges: `id_ranges`: Array of [start, end] ranges
- Callsign Patterns: `callsigns`: Array of patterns with "*" wildcards

### Configuration Options

| Option | Type | Description |
|--------|------|-------------|
| `enabled` | boolean | Whether this configuration is active |
| `timeout` | number | Seconds before timing out inactive repeaters |
| `passphrase` | string | Authentication key for the repeater |
| `talkgroups` | array | List of allowed talkgroup IDs |
| `description` | string | Human-readable description |

### Pattern Matching Priority

When multiple patterns could match a repeater, they are evaluated in this order:
1. Specific IDs (highest priority)
2. ID Ranges
3. Callsign patterns (lowest priority)

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

## Configuration Tips

1. **Security**:
   - Use strong passphrases for repeater authentication
   - Restrict talkgroups appropriately for each repeater group
   - Use blacklisting to prevent unauthorized access

2. **Organization**:
   - Give patterns clear, descriptive names
   - Document the purpose of each pattern in its description
   - Group related repeaters under common patterns

3. **Maintenance**:
   - Keep the default configuration restrictive
   - Document blacklist reasons for future reference
   - Use meaningful talkgroup names

4. **Validation**:
   - Ensure no pattern has multiple match types
   - Verify talkgroup IDs exist in the talkgroups section
   - Check for overlapping ID ranges

## Example Configuration

See the `config/hblink.json` file in the repository for a complete example configuration with multiple patterns and talkgroups.
