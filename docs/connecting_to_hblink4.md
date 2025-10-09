# Connecting to HBlink4

This guide explains how to connect repeaters and hotspots to an HBlink4 server.

## Overview

HBlink4 uses the HomeBrew DMR protocol for communication between repeaters/hotspots and the server. The connection process involves authentication, configuration exchange, and optional talkgroup subscription.

## Connection Process

### 1. Basic Connection Flow

```
Repeater                    Server
   |                         |
   |---------- RPTL -------->| (Login Request)
   |<-------- MSTCL ---------|  (Challenge)
   |---------- RPTK -------->| (Authentication)
   |---------- RPTC -------->| (Configuration)
   |<-------- RPTACK --------|  (Accept)
   |                         |
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

Before connecting, the server administrator must add your repeater to their `config.json`, or use a "range" configuration that would include your repeater by radio ID range and/or callsign, including widlcards:

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

## Firewall and NAT Configuration

### For HBlink4 Server Administrators

If you're hosting an HBlink4 server, you need to configure your firewall and router to allow incoming connections.

#### Required Ports

**HBlink4 Server:**
- **UDP 62031** - IPv4 repeater connections (default)
- **UDP 62032** - IPv6 repeater connections (default, if IPv6 enabled)

**Dashboard (optional, for remote access):**
- **TCP 8080** - Web dashboard HTTP (default, both IPv4 and IPv6)
- Or use a reverse proxy (nginx/apache) on port 80/443

#### Firewall Configuration

Configure your firewall to allow the following inbound traffic:

```
# HBlink4 repeater ports (applies to both IPv4 and IPv6)
ALLOW: Protocol=UDP4, Port=62031, Direction=INBOUND  # IPv4 repeaters
ALLOW: Protocol=UDP6, Port=62032, Direction=INBOUND  # IPv6 repeaters (if enabled)

# Dashboard web interface (applies to both IPv4 and IPv6)
ALLOW: Protocol=TCP*, Port=8080, Direction=INBOUND   # Dashboard HTTP/WebSocket
```

**Note:** Some firewalls automatically handle both IPv4 and IPv6 with a single rule per port. If your firewall requires separate rules for each protocol, you may need to duplicate rules for IPv4 and IPv6. In the information here, we use a 4, 6, or * to indicate one, the other or both.

Apply these rules using your firewall management tool (iptables, firewalld, ufw, Windows Firewall, cloud security groups, etc.).

#### Router/NAT Port Forwarding

If your HBlink4 server is behind a router/NAT:

1. **Log into your router** (typically http://192.168.1.1 or similar)
2. **Find Port Forwarding** section (may be called "Virtual Server", "NAT", or "Applications")
3. **Add port forwarding rules**:

```
Forward: External_Port=62031 -> Internal_IP=[server], Internal_Port=62031, Protocol=UDP4
Forward: External_Port=62032 -> Internal_IP=[server], Internal_Port=62032, Protocol=UDP6
Forward: External_Port=8080 -> Internal_IP=[server], Internal_Port=8080, Protocol=TCP* (optional)
```

**Example:**
```
Service Name: HBlink4-IPv4
External Port: 62031
Internal Port: 62031
Protocol: UDP
Internal IP: 192.168.1.100  # Your server's local IP address
```

### For Repeater/Hotspot Operators

If you're connecting TO an HBlink4 server, you typically don't need any special firewall configuration since you're making outbound connections. However you should be careful to note that the UDP session timer in your firewall must be longer than the interval at which your repater sends pings to the server, or the state entry could be removed, and you will not receive trafic from the server back to your repaeter.

#### Outbound Traffic Requirements

Your repeater/hotspot needs to make outbound connections:

```
ALLOW: Protocol=UDP, Destination=[server-ip], Port=62031, Direction=OUTBOUND
```

Most firewalls with stateful packet inspection will automatically allow the return traffic.

### Troubleshooting Network Issues

**Dashboard not accessible:**
- Verify dashboard service is running: `sudo systemctl status hblink4-dash`
- Check firewall allows TCP 8080
- Confirm dashboard is binding to 0.0.0.0 (not just 127.0.0.1)
- Review dashboard logs: `journalctl -u hblink4-dash`

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
- TS1 active TGs: `1, 2, 3` (subset of allowed TGs)
- TS2 active TGs: `10, 20` (subset of allowed TGs)

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

If you want to use all configured TGs, simply **don't send RPTO**. The server will use the full list from the config.

Alternatively, you can send RPTO with all TGs from the config:
```
RPTO + [radio_id] + "TS1=1,2,3,4,5;TS2=10,20,30"
```
Both approaches result in the same behavior.

#### Example 4: Disable a Timeslot via RPTO

Server config allows:
```json
"slot1_talkgroups": [1, 2, 3, 4, 5],
"slot2_talkgroups": [10, 20, 30]
```

Repeater sends (note: TS1 has empty value):
```
RPTO + [radio_id] + "TS1=;TS2=10,20"
```

Result:
- TS1 active TGs: `[]` (empty set - **no traffic accepted on TS1**)
- TS2 active TGs: `10, 20`

⚠️ **Note**: An empty TS value in RPTO (e.g., `TS1=`) results in no TGs being active on that slot. This is useful for temporarily disabling a timeslot without changing server config.

### When to Use RPTO

**Use RPTO when:**
- Repeater wants to subscribe to a subset of allowed TGs
- Different talkgroups are needed for different times/events
- Repeater has limited capacity and wants selective routing
- Implementing dynamic TG subscription based on local user preferences
- Need to temporarily disable a timeslot (send empty TG list)

**Don't use RPTO when:**
- You want all configured talkgroups (just connect normally - RPTO is optional)
- Trying to add TGs not in server config (won't work - config is master allow list)

**Understanding Allow All vs Deny All:**

| Config Setting | RPTO Behavior | Result |
|----------------|---------------|--------|
| Config has TG list `[1,2,3]` | No RPTO sent | Use all TGs from config: `[1,2,3]` |
| Config has TG list `[1,2,3]` | RPTO: `TS1=1,2` | Use intersection: `[1,2]` |
| Config has TG list `[1,2,3]` | RPTO: `TS1=` (empty) | Use intersection: `[]` (deny all on TS1) |
| Config has empty list `[]` | No RPTO sent | **Deny all** (empty config = deny all) |
| Config has empty list `[]` | RPTO: `TS1=1,2,3` | Still `[]` (config is master - denies all) |
| Config not defined (missing) | No RPTO sent | **Allow all** (no config = allow all) |
| Config not defined (missing) | RPTO: `TS1=1,2` | Use RPTO list: `[1,2]` |

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

- **MMDVMHost** - The original implmentation of Homebrew Repeater Protocol, and under the hood of most/all below
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
