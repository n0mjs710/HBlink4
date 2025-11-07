# Outbound Connections - Technical Design Document

**Branch:** `outbound-connection`  
**Status:** Design Complete, Implementation In Progress  
**Date:** November 7, 2025

## Overview

Add capability for HBlink4 to act as a **client** connecting to other HomeBrew servers, while continuing to accept inbound repeater connections. This enables server-to-server linking for network expansion.

**Key Principle:** From routing perspective, outbound connections are identical to inbound repeaters - just endpoints for DMRD traffic. From connection management perspective, we initiate and maintain the connection instead of accepting it.

## Use Case

HBlink4 server wants to connect to another DMR server (HBlink4, HBlink3, or compatible HomeBrew server) to exchange traffic. The outbound connection appears to the remote server as an MMDVM repeater connecting to it.

## Configuration

### Config Schema

New top-level section `"outbound_connections": []` (separate from `access_control.repeaters`):

```json
{
  "outbound_connections": [
    {
      "enabled": true,
      "name": "Link-to-K0USY",
      "address": "hblink.k0usy.org",
      "port": 62031,
      "our_id": 3120101,
      "password": "secret123",
      "options": "TS1=*;TS2=*",
      
      "callsign": "K0USY",
      "rx_frequency": 449375000,
      "tx_frequency": 444375000,
      "power": 25,
      "latitude": 38.96452,
      "longitude": -95.31797,
      "height": 3,
      "location": "Lawrence, KS",
      "description": "Multi-Mode Repeater",
      "url": "k0usy.org"
    }
  ]
}
```

### Field Definitions

**Connection Control:**
- `enabled` (bool, required) - Enable/disable without removing config
- `name` (string, required) - Unique identifier for logs/dashboard
- `address` (string, required) - Remote server IP or DNS hostname
- `port` (int, required) - Remote server port (typically 62031)

**Authentication:**
- `our_id` (int, required) - DMR ID we present to remote server
- `password` (string, required) - Passkey for authentication

**Talkgroup Filtering:**
- `options` (string, optional) - TG filter in RPTO format
  - `"TS1=1,2,3;TS2=10,20"` - Specific TGs per slot
  - `"TS1=*;TS2=*"` - All TGs both slots
  - `"TS1=;TS2="` - No TGs (deny all)
  - `""` or `null` - No TGs (deny all)
  - Missing TS defaults to empty (deny): `"TS1=1,2"` → TS2=[]

**Metadata (sent to remote in RPTC):**
All metadata fields required (use defaults if not specified):
- `callsign` (string) - Default: `""`
- `rx_frequency` (int) - Default: `0`
- `tx_frequency` (int) - Default: `0`
- `power` (int) - Default: `0`
- `latitude` (float) - Default: `0.0`
- `longitude` (float) - Default: `0.0`
- `height` (int) - Default: `0`
- `location` (string) - Default: `""`
- `description` (string) - Default: `""`
- `url` (string) - Default: `""`

**Note:** Remote server expects all metadata fields (mimicking MMDVM repeater). If remote rejects empty/zero values, admin must provide valid data.

### Options String Behavior

The `options` field serves **dual purpose**:
1. **Signals remote server** which TGs we want (sent via RPTO)
2. **Filters locally** what traffic we send/receive on this connection

**Wildcard `*` = Allow All:**
- `"TS1=*"` → `slot1_talkgroups = None` (O(1) performance, no filtering)
- Empty or missing → `slot1_talkgroups = set()` (deny all)

## State Management

### Data Structures

**Three dictionaries for peer tracking:**

```python
# Inbound repeaters (keyed by repeater_id)
self._repeaters: Dict[bytes, RepeaterState] = {}

# Outbound connections (keyed by connection name)
self._outbounds: Dict[str, RepeaterState] = {}

# Reverse lookup for O(1) packet routing (keyed by remote server address)
self._outbound_by_addr: Dict[Tuple[str, int], str] = {}

# ID reservation (for protection against DoS)
self._outbound_ids: Set[bytes] = set()
```

**Why separate dictionaries:**
1. **Multiple outbounds can use same `our_id`** (we're one system connecting to multiple servers)
2. **Protection:** Outbounds are admin-configured, must not be kicked by inbound repeaters
3. **Routing:** Combine both for route cache building

### State Class Reuse

**Use existing `RepeaterState` class for outbound connections.**

Justification:
- Semantically correct (we're mimicking a repeater)
- Has all needed fields (connection state, auth, metadata, TGs, streams)
- No rename needed (avoids risk of missing references)

For outbound connections:
- `repeater_id` = our configured `our_id`
- `ip`, `port` = remote server address
- `sockaddr` = remote server address (for sending packets)
- All other fields function identically

### ID Conflict Protection

**Security requirement:** Prevent inbound repeaters from DoS attacking outbound connections.

**Implementation:**

```python
# At startup, populate reserved IDs
for name, config in outbound_configs:
    our_id_bytes = config['our_id'].to_bytes(4, 'big')
    self._outbound_ids.add(our_id_bytes)

# In _handle_repeater_login:
if repeater_id in self._outbound_ids:
    LOGGER.warning(f'⛔ Rejecting inbound repeater {self._rid_to_int(repeater_id)} '
                   f'from {ip}:{port} - ID reserved for outbound connection')
    self._send_nak(repeater_id, addr, reason="ID reserved for outbound connection")
    return
```

**Priority:** Outbound connections (admin-configured) > Inbound repeaters (untrusted)

## Connection Lifecycle

### Startup Sequence

```
On HBlink4 startup:
1. Parse config, create RepeaterState for each enabled outbound
2. Populate _outbound_ids with all our_id values
3. Initiate all outbound connections immediately (no stagger)
4. For each outbound:
   - Resolve DNS if needed
   - Send RPTL (login) to remote
   - Wait for MSTCL (challenge) response
   - Calculate auth response, send RPTK
   - Send RPTC (config with all metadata)
   - If options configured, send RPTO
   - Enter RPTPING keepalive loop
```

### Connection States

Same states as inbound repeaters:
- `login` - Sent RPTL, waiting for challenge
- `config` - Authenticated, sending config
- `connected` - Fully established, exchanging traffic

### Reconnection Logic

**On disconnect:**
1. Log disconnection at INFO level
2. Wait one **keepalive interval** (reuse existing config parameter)
3. Attempt reconnection (send RPTL)
4. Repeat indefinitely until connected

**Rationale:**
- Keepalive interval is convenient, doesn't race connection attempts
- Orderly reconnection attempts
- Problem may resolve (network/DNS/remote server comes back)
- Keeps logs noisy (admin needs to see persistent failures)

### Graceful Shutdown

On HBlink4 shutdown:
- Send RPTCL (disconnect message) to all connected outbounds
- Clean disconnect from remote servers
- Log at INFO level

### Error Handling

**Authentication Failure:**
- Log at ERROR level with full details
- Keep retrying (admin may fix remote config)
- Show error on dashboard connection card

**DNS Resolution Failure:**
- Log at ERROR level
- Keep retrying (DNS may come back)
- Show error on dashboard

**Network Unreachable:**
- Log at ERROR level
- Keep retrying (network may recover)
- Show error on dashboard

**No response to RPTPING:**
- Same logic as inbound repeaters (track missed_pings)
- After threshold, consider disconnected
- Trigger reconnection logic

## Protocol Implementation

### Client-Side Protocol Flow

```
HBlink4 (us)                Remote Server
   |                              |
   |---------- RPTL ------------->| (Login Request)
   |<--------- MSTCL -------------|  (Challenge with salt)
   |---------- RPTK ------------->| (Auth Response: sha256(password + salt))
   |---------- RPTC ------------->| (Config: all metadata fields)
   |<--------- RPTACK ------------|  (Accept)
   |---------- RPTO ------------->| (Options: if configured)
   |<--------- RPTACK ------------|  (Accept)
   |                              |
   |-------- RPTPING ------------>| (Keepalive)
   |<------- MSTPONG -------------|
   |                              |
   |<------- DMRD ----------------|  (Voice/data traffic)
   |-------- DMRD -------------->|
```

### Packet Construction

**RPTL (Login):**
```python
packet = RPTL + our_id_bytes  # 4 bytes: 'RPTL' + 4 bytes DMR ID
```

**RPTK (Auth Response):**
```python
# Remote sent: MSTCL + salt (4 bytes)
salt = received_packet[4:8]
auth_hash = sha256(password.encode() + salt).digest()
packet = RPTK + our_id_bytes + auth_hash  # 'RPTK' + 4 bytes ID + 32 bytes hash
```

**RPTC (Config):**
Same format as inbound repeaters send - pack all metadata fields per protocol spec.

**RPTO (Options):**
```python
options_str = config['options']  # e.g., "TS1=1,2,3;TS2=10,20"
packet = RPTO + our_id_bytes + options_str.encode('utf-8')
```

**RPTPING (Keepalive):**
```python
packet = RPTPING + our_id_bytes
```

### Receiving Packets from Remote

In `datagram_received(data, addr)`:

```python
# Check if packet is from an outbound connection
connection_name = self._outbound_by_addr.get((addr[0], addr[1]))
if connection_name:
    outbound = self._outbounds[connection_name]
    # Process packet (MSTCL, RPTACK, MSTPONG, DMRD, etc.)
    # Apply TG filters from outbound.slot1_talkgroups, slot2_talkgroups
```

**O(1) lookup** via `_outbound_by_addr` ensures no performance impact on packet processing.

## Routing Integration

### Route Cache Building

**No functional difference** from inbound repeaters - outbounds are just more endpoints:

```python
def _build_route_cache_for_tg(self, tg: int, slot: int) -> List[RepeaterState]:
    targets = []
    
    # Add inbound repeaters
    for repeater_id, repeater in self._repeaters.items():
        if self._tg_allowed(repeater, tg, slot):
            targets.append(repeater)
    
    # Add outbound connections
    for name, outbound in self._outbounds.items():
        if self._tg_allowed(outbound, tg, slot):
            targets.append(outbound)
    
    return targets
```

### Talkgroup Filtering

**Same logic as repeaters:**
- `slot1_talkgroups = None` → Allow all (no filtering)
- `slot1_talkgroups = set()` → Deny all (no traffic)
- `slot1_talkgroups = {1,2,3}` → Allow only these TGs

**Dual filtering:**
1. **Outbound send:** Before forwarding DMRD to outbound, check if TG allowed
2. **Outbound receive:** When receiving DMRD from outbound, check if TG allowed

### Loop Prevention

**Not our responsibility.** System operators must coordinate network topology to avoid loops. Same principle as existing inbound repeater routing.

## Dashboard

### Display Requirements

**New "Outbound Connections" table** (separate from Repeaters):

**Fields shown:**
- Connection name (from config)
- Remote server address:port
- Connection status (connected/disconnected/error)
- Our ID (what we present as)
- Active TGs (TS1, TS2)
- Last heard timestamp
- Missed ACKs count (equivalent to missed pings)
- Error message (if any: "Auth Failed", "DNS Error", etc.)

**Color coding:**
- **Blue/Cyan:** Connected and healthy
- **Yellow:** Connected but missed ACKs (same as missed pings for repeaters)
- **Dark Blue/Gray:** Disconnected or connecting
- **Red text:** Error message displayed

**Always shown:** All enabled outbound connections appear in table (unlike repeaters which only show when connected).

### Dashboard Events

**New event types:**
- `outbound_connected` - Outbound connection established
- `outbound_disconnected` - Outbound connection lost
- `outbound_tg_update` - TG configuration changed (if RPTO sent)
- `outbound_error` - Connection error (auth, DNS, network)

**Event payload includes:**
- `connection_name`
- `our_id`
- `remote_address`
- `remote_port`
- `slot1_talkgroups`, `slot2_talkgroups`
- `error_message` (if applicable)

## Logging

### Log Levels

**INFO:**
- Connection established: `"🔗 Outbound connection 'Link-to-K0USY' connected to hblink.k0usy.org:62031"`
- Disconnection: `"🔌 Outbound connection 'Link-to-K0USY' disconnected"`
- Reconnection attempts: `"🔄 Attempting to reconnect outbound 'Link-to-K0USY'"`
- RPTO sent: `"📋 Sent RPTO to 'Link-to-K0USY': TS1=1,2,3;TS2=10,20"`

**DEBUG:**
- RPTPING sent (keepalive traffic)
- MSTPONG received
- Packet routing decisions (same verbosity as repeaters)

**ERROR:**
- Authentication failures: `"⛔ Outbound 'Link-to-K0USY' auth failed - check password"`
- DNS resolution failures: `"⚠️ Outbound 'Link-to-K0USY' DNS lookup failed for hblink.example.com"`
- Network unreachable: `"🚫 Outbound 'Link-to-K0USY' network unreachable"`
- Protocol errors: `"❌ Outbound 'Link-to-K0USY' unexpected response to RPTL"`

**WARNING:**
- ID conflict detected: `"⚠️ Inbound repeater 123456 rejected - ID reserved for outbound"`
- TG filtering edge cases (if any arise)

### Log Context

Always include:
- Connection name (primary identifier)
- Our ID (for disambiguation if multiple outbounds use same ID)
- Remote address (for network debugging)

## Implementation Checklist

### Phase 1: Configuration
- [ ] Add `OutboundConnectionConfig` dataclass
- [ ] Parse `outbound_connections` from config.json
- [ ] Validate required fields (name, address, port, our_id, password)
- [ ] Apply defaults for optional metadata fields
- [ ] Parse options string (TS1=, TS2=, support `*` wildcard)

### Phase 2: State Management
- [ ] Add `self._outbounds` dictionary (keyed by name)
- [ ] Add `self._outbound_by_addr` reverse mapping
- [ ] Add `self._outbound_ids` protection set
- [ ] Populate outbound states at startup

### Phase 3: Connection Management
- [ ] Implement DNS resolution (async)
- [ ] Implement RPTL sending (login)
- [ ] Handle MSTCL response (challenge)
- [ ] Implement RPTK auth response (sha256)
- [ ] Implement RPTC config sending
- [ ] Implement RPTO options sending (if configured)
- [ ] Track connection state transitions (login → config → connected)

### Phase 4: Keepalive
- [ ] Add periodic RPTPING sending (per outbound)
- [ ] Handle MSTPONG responses
- [ ] Track missed ACKs (same logic as missed pings)
- [ ] Trigger reconnection on timeout

### Phase 5: Packet Routing
- [ ] Modify `datagram_received()` to check `_outbound_by_addr`
- [ ] Route DMRD from outbounds through normal routing logic
- [ ] Apply TG filters when sending to outbounds
- [ ] Apply TG filters when receiving from outbounds

### Phase 6: Protection
- [ ] Check `_outbound_ids` in `_handle_repeater_login`
- [ ] Send NAK to inbound repeaters with conflicting IDs
- [ ] Log ID conflicts at WARNING level

### Phase 7: Reconnection
- [ ] Implement reconnection timer (use keepalive interval)
- [ ] Handle DNS re-resolution on reconnect
- [ ] Log reconnection attempts at INFO level
- [ ] Retry indefinitely (until connected or disabled)

### Phase 8: Error Handling
- [ ] Catch and log auth failures (ERROR)
- [ ] Catch and log DNS failures (ERROR)
- [ ] Catch and log network errors (ERROR)
- [ ] Update dashboard with error status
- [ ] Continue retrying despite errors

### Phase 9: Dashboard Integration
- [ ] Add outbound connection events (connected, disconnected, error, tg_update)
- [ ] Emit events on state changes
- [ ] Include full connection details in event payload
- [ ] Add connection name to all event payloads

### Phase 10: Shutdown
- [ ] Send RPTCL to all connected outbounds on shutdown
- [ ] Log graceful disconnection
- [ ] Clean up state

### Phase 11: Testing
- [ ] Test single outbound connection
- [ ] Test multiple outbound connections
- [ ] Test same our_id on multiple outbounds
- [ ] Test ID conflict protection (inbound blocked)
- [ ] Test reconnection after disconnect
- [ ] Test DNS resolution (both success and failure)
- [ ] Test auth failure handling
- [ ] Test TG filtering (send and receive)
- [ ] Test options string parsing (*, empty, missing TS)
- [ ] Test dashboard display and events
- [ ] Test graceful shutdown

## Performance Considerations

**O(1) Packet Routing:**
- `_outbound_by_addr` dictionary ensures constant-time lookup
- Critical for real-time voice (jitter > latency concern)
- Trade-off: ~100 bytes RAM per outbound vs O(n) loop per packet

**TG Filtering:**
- `None` (allow all) = no filtering overhead
- Set lookup = O(1) per packet
- Use `*` wildcard liberally for minimal processing

**Connection Overhead:**
- Outbounds add to routing tables (more route cache entries)
- Each outbound has own keepalive task
- Expect small number of outbounds (< 10 typical)

## Security Considerations

**ID Reservation:**
- Prevents DoS by malicious inbound repeaters
- Admin-configured outbounds cannot be kicked
- Protects network stability

**Authentication:**
- Same challenge-response as repeaters (sha256)
- Password security is admin responsibility
- Failed auth logged at ERROR (visible, actionable)

**TG Filtering:**
- Outbounds bypass access_control (admin trusted)
- TG filters still applied (admin controls what traffic passes)
- Conservative default: missing TS = deny all (explicit config required)

## Future Enhancements

**Not in scope for initial implementation:**
- Config reload without restart
- Staggered startup for many outbounds
- Connection health metrics (latency, packet loss)
- Automatic failover between redundant outbounds
- TLS/encryption for outbound connections
- IPv6 support (depends on DNS resolution implementation)

## Open Questions

**None.** All design questions resolved through discussion.

## References

- Protocol specification: `docs/protocol.md`
- Existing repeater connection code: `hblink4/hblink.py` (lines 1070-1400)
- MMDVM.ini format: Standard HomeBrew repeater config

---

**Document Status:** Design complete, ready for implementation  
**Next Step:** Begin Phase 1 implementation (Configuration parsing)
