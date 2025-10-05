# Connecting to HBlink4

This guide explains how to connect repeaters and hotspots to an HBlink4 server.

## Overview

HBlink4 uses the HomeBrew DMR protocol for communication between repeaters/hotspots and the server. The connection process involves authentication, configuration exchange, and optional talkgroup subscription.

## Connection Process

### 1. Basic Connection Flow

```
Repeater                    Server
   |                          |
   |---------- RPTL -------->| (Login Request)
   |<-------- MSTCL ---------|  (Challenge)
   |---------- RPTK -------->| (Authentication)
   |---------- RPTC -------->| (Configuration)
   |<-------- RPTACK --------|  (Accept)
   |                          |
   |-------- RPTPING ------->| (Keepalive)
   |<------- MSTPONG --------|
```

### 2. Required Information

To connect to an HBlink4 server, you need:

- **Server IP/Hostname**: The address of the HBlink4 server
- **Server Port**: Default is 62031 (IPv4) and/or 62032 (IPv6)
- **Repeater ID**: Your DMR radio ID (32-bit integer)
- **Passkey**: Authentication key (provided by server administrator)
- **Callsign**: Your amateur radio callsign
- **Location**: Station location
- **Frequencies**: TX and RX frequencies (if applicable)

### 3. Server Configuration Requirements

Before connecting, the server administrator must add your repeater to their `config.json`:

```json
{
  "access_control": {
    "repeaters": [
      {
        "id": 1234567,
        "callsign": "W1ABC",
        "passkey": "your-secret-passkey",
        "slot1_talkgroups": [1, 2, 3, 91],
        "slot2_talkgroups": [10, 20, 30]
      }
    ]
  }
}
```

## Dynamic Talkgroup Subscription (RPTO)

HBlink4 supports dynamic talkgroup subscription via the **RPTO** (Repeater Options) command. This allows repeaters to request specific talkgroups without requiring server configuration changes.

### How RPTO Works

1. Repeater connects and authenticates normally
2. After connection is established, repeater sends RPTO message
3. Server intersects requested TGs with configured allowed TGs
4. Only TGs present in **both** lists are accepted
5. Server responds with RPTACK

**Important**: The server configuration acts as the master allow list. RPTO can only restrict or select from the configured TGs, not add new ones.

### RPTO Message Format

```
RPTO + [4-byte radio_id] + "TS1=tg1,tg2,tg3;TS2=tg4,tg5,tg6"
```

**Format specification:**
- Command: `RPTO` (4 bytes, ASCII)
- Radio ID: 4-byte DMR ID (big-endian)
- Options string: ASCII text with format `TS1=<tgs>;TS2=<tgs>`
  - `TS1=` - Timeslot 1 talkgroups (comma-separated)
  - `TS2=` - Timeslot 2 talkgroups (comma-separated)
  - Separated by semicolon (`;`)

### RPTO Examples

#### Example 1: Request Subset of TGs

Server config allows:
```json
"slot1_talkgroups": [1, 2, 3, 4, 5, 91, 310],
"slot2_talkgroups": [10, 20, 30, 40, 50]
```

Repeater sends:
```
RPTO + [radio_id] + "TS1=1,2,3;TS2=10,20"
```

Result:
- TS1 active TGs: `1, 2, 3`
- TS2 active TGs: `10, 20`

#### Example 2: Request TGs Not in Config (Rejected)

Server config allows:
```json
"slot1_talkgroups": [1, 2, 3],
"slot2_talkgroups": [10, 20, 30]
```

Repeater sends:
```
RPTO + [radio_id] + "TS1=1,2,3,91;TS2=10,99"
```

Result:
- TS1 active TGs: `1, 2, 3` (91 rejected - not in config)
- TS2 active TGs: `10` (99 rejected - not in config)
- Server logs warning about rejected TGs

#### Example 3: Using All Configured TGs

Repeater can send RPTO with all TGs from config, or simply not send RPTO at all - both result in using all configured TGs.

### When to Use RPTO

**Use RPTO when:**
- Repeater wants to subscribe to a subset of allowed TGs
- Different talkgroups are needed for different times/events
- Repeater has limited capacity and wants selective routing
- Implementing dynamic TG subscription based on local user preferences

**Don't use RPTO when:**
- You want all configured talkgroups (just connect normally)
- Trying to add TGs not in server config (won't work - config is master)

### RPTO Timing

- RPTO can be sent **any time after authentication** (CONNECTED state)
- RPTO can be sent **multiple times** to change subscriptions
- Changes take effect immediately
- Server responds with RPTACK to acknowledge

### Server-Side Behavior

When server receives RPTO:
1. Parses `TS1=` and `TS2=` talkgroup lists
2. Performs set intersection with configured allowed TGs
3. Updates repeater's active talkgroup subscriptions
4. Logs rejected TGs (if any)
5. Emits event to update dashboard in real-time
6. Sends RPTACK response

The dashboard will show an indicator when a repeater has sent RPTO (TG lists will be highlighted in blue).

## Connection Maintenance

### Keepalive Messages

Repeaters must send **RPTPING** messages periodically to maintain the connection:

- Default interval: Every 5-30 seconds (configurable)
- Server responds with **MSTPONG**
- Server tracks missed pings
- After 3 missed pings (default), repeater is disconnected

### Reconnection

If disconnected:
1. Wait a few seconds before reconnecting
2. Restart the connection process from RPTL (login)
3. Re-send RPTO if you were using dynamic TG subscription

## Compatible Software

HBlink4 is compatible with:

- **Pi-Star** - Set mode to "Homebrew" and configure master server
- **MMDVM_Bridge** - Use HBlink protocol mode
- **OpenGD77** hotspots - Configure as Homebrew/HBlink
- **DVMega** - Use HBlink/Homebrew mode
- **WPSD** - Configure DMR master as Homebrew protocol
- **Custom implementations** - Follow the protocol specification in `docs/protocol.md`

## Troubleshooting

### Connection Refused

**Problem**: Cannot connect to server

**Solutions**:
- Verify server IP and port
- Check firewall allows UDP on port 62031/62032
- Confirm server is running: `systemctl status hblink4`
- Check server logs: `journalctl -u hblink4 -f`

### Authentication Failed

**Problem**: Connection drops after login

**Solutions**:
- Verify passkey matches server configuration
- Confirm your radio ID is in server's access control list
- Check for typos in callsign or ID
- Review server logs for authentication errors

### No Audio/Traffic

**Problem**: Connected but no audio passing

**Solutions**:
- Verify talkgroups are configured on both repeater and server
- Check that your TGs match server's slot1_talkgroups/slot2_talkgroups
- If using RPTO, verify requested TGs are in server config
- Check dashboard to see active talkgroups
- Ensure timeslot matches (TS1 or TS2)

### RPTO Not Working

**Problem**: RPTO sent but talkgroups not updated

**Solutions**:
- Verify connection is in CONNECTED state (not just authenticated)
- Check RPTO message format: `TS1=1,2,3;TS2=10,20`
- Confirm requested TGs exist in server configuration
- Review server logs for RPTO parsing errors
- Check that TGs aren't being rejected (server logs will show warnings)

### Frequent Disconnections

**Problem**: Repeater keeps disconnecting

**Solutions**:
- Verify keepalive (RPTPING) is being sent regularly
- Check network stability and latency
- Ensure server is not overloaded
- Review server timeout settings
- Check for IP address changes (DHCP issues)

## Security Considerations

- **Passkeys**: Use strong, unique passkeys for each repeater
- **Firewall**: Limit access to trusted repeater IPs when possible
- **Monitoring**: Watch server logs for suspicious connection attempts
- **Config Security**: Protect your `config.json` file (contains passkeys)
- **Updates**: Keep HBlink4 updated for security patches

## Getting Help

If you need assistance:

1. Check server logs: `journalctl -u hblink4 -n 100`
2. Review HBlink4 documentation in `docs/`
3. Check protocol specification: `docs/protocol.md`
4. Contact your server administrator
5. Visit: https://github.com/n0mjs710/HBlink4

## Reference Documents

- **Protocol Specification**: `docs/protocol.md` - Complete protocol details
- **Configuration Guide**: `docs/configuration.md` - Server setup
- **Access Control**: `docs/configuration.md#access-control` - Adding repeaters
- **Routing**: `docs/routing.md` - How traffic is routed
