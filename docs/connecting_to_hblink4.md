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

## Firewall and NAT Configuration

### For HBlink4 Server Administrators

If you're hosting an HBlink4 server, you need to configure your firewall and router to allow incoming connections.

#### Required Ports

**HBlink4 Server:**
- **UDP 62031** - IPv4 repeater connections (default)
- **UDP 62032** - IPv6 repeater connections (default, if IPv6 enabled)
  - Note: Most firewalls don't require separate IPv4/IPv6 rules per port
  - If using same port for both (e.g., 62031), one rule typically covers both protocols

**Dashboard (optional, for remote access):**
- **TCP 8080** - Web dashboard HTTP (default, both IPv4 and IPv6)
- Or use a reverse proxy (nginx/apache) on port 80/443

#### Firewall Configuration

Configure your firewall to allow the following inbound traffic:

```
# HBlink4 repeater ports (applies to both IPv4 and IPv6)
ALLOW: Protocol=UDP, Port=62031, Direction=INBOUND  # IPv4 repeaters
ALLOW: Protocol=UDP, Port=62032, Direction=INBOUND  # IPv6 repeaters (if enabled)

# Dashboard web interface (applies to both IPv4 and IPv6)
ALLOW: Protocol=TCP, Port=8080, Direction=INBOUND   # Dashboard HTTP/WebSocket
```

**Note:** Most modern firewalls automatically handle both IPv4 and IPv6 with a single rule per port. If your firewall requires separate rules for each protocol, you may need to duplicate rules for IPv4 and IPv6.

Apply these rules using your firewall management tool (iptables, firewalld, ufw, Windows Firewall, cloud security groups, etc.).

#### Router/NAT Port Forwarding

If your HBlink4 server is behind a router/NAT:

1. **Log into your router** (typically http://192.168.1.1 or similar)
2. **Find Port Forwarding** section (may be called "Virtual Server", "NAT", or "Applications")
3. **Add port forwarding rules**:

```
Forward: External_Port=62031 -> Internal_IP=[server], Internal_Port=62031, Protocol=UDP
Forward: External_Port=62032 -> Internal_IP=[server], Internal_Port=62032, Protocol=UDP
Forward: External_Port=8080 -> Internal_IP=[server], Internal_Port=8080, Protocol=TCP (optional)
```

**Example:**
```
Service Name: HBlink4-IPv4
External Port: 62031
Internal Port: 62031
Protocol: UDP
Internal IP: 192.168.1.100  # Your server's local IP address
```

#### Testing Connectivity

**From outside your network:**

```bash
# Test if port is reachable (from remote machine)
nc -vuz your-public-ip 62031
nc -vuz your-public-ip 62032

# Or use online port checking tools
# https://www.yougetsignal.com/tools/open-ports/
```

**Check your public IP:**
```bash
curl ifconfig.me
# or
curl icanhazip.com
```

### For Repeater/Hotspot Operators

If you're connecting TO an HBlink4 server, you typically don't need any special firewall configuration since you're making outbound connections.

#### Outbound Traffic Requirements

Your repeater/hotspot needs to make outbound connections:

```
ALLOW: Protocol=UDP, Destination=[server-ip], Port=62031, Direction=OUTBOUND
```

Most firewalls with stateful packet inspection will automatically allow the return traffic.

### Cloud Hosting Considerations

#### AWS (Amazon Web Services)

Configure **Security Group** inbound rules:

```
Type: Custom UDP, Port: 62031, Source: 0.0.0.0/0 (or specific IPs)
Type: Custom UDP, Port: 62032, Source: 0.0.0.0/0
Type: Custom TCP, Port: 8080, Source: 0.0.0.0/0 (dashboard, optional)
```

Ensure **Network ACL** allows UDP traffic.

#### Azure

Add **Inbound Port Rules** to Network Security Group:

```
Port: 62031, Protocol: UDP, Source: Any
Port: 62032, Protocol: UDP, Source: Any
Port: 8080, Protocol: TCP, Source: Any (optional)
```

#### Google Cloud Platform (GCP)

Create **Firewall Rule**:

```
Direction: Ingress
Targets: All instances (or specific tags)
Source: 0.0.0.0/0
Protocols and ports: udp:62031,62032; tcp:8080
```

#### DigitalOcean

Configure **Cloud Firewalls**:

```
Inbound: UDP ports 62031, 62032
Inbound: TCP port 8080 (optional)
Source: All IPv4 / All IPv6
```

### Dashboard Access Security

If exposing the dashboard publicly, consider:

1. **Use a reverse proxy** with HTTPS (nginx/apache + Let's Encrypt)
2. **Add authentication** (HTTP basic auth or OAuth)
3. **Restrict by IP** if accessing from known locations
4. **Use VPN** for secure remote access
5. **Keep dashboard on localhost** and access via SSH tunnel:

```bash
# SSH tunnel from remote machine
ssh -L 8080:localhost:8080 user@your-server-ip

# Then access http://localhost:8080 in your browser
```

### Troubleshooting Network Issues

**Cannot connect to server:**
- Verify server IP and port are correct
- Check firewall rules on server (ensure UDP 62031/62032 allowed)
- Verify NAT/port forwarding if behind router
- Test with `nc -vuz server-ip port`
- Check server is actually running: `sudo systemctl status hblink4`

**Intermittent disconnections:**
- Check for NAT session timeouts (increase timeout on router)
- Verify keepalive packets are being sent
- Check network stability and packet loss
- Review MTU settings (especially over VPN)

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
