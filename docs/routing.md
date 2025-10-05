# Call Routing and Forwarding

## Overview

HBlink4 implements configuration-based call routing using timeslot-specific talkgroup lists. This enables precise control over which calls are accepted from repeaters and forwarded to them.

## Configuration Structure

### Repeater Configuration

Each repeater configuration includes slot-specific talkgroup lists that define which TGIDs are allowed on each timeslot:

```json
{
    "name": "Example Repeater",
    "match": {
        "ids": [312000]
    },
    "config": {
        "enabled": true,
        "timeout": 30,
        "passphrase": "passw0rd",
        "slot1_talkgroups": [8, 9],
        "slot2_talkgroups": [3120, 3122],
        "description": "KS-DMR Network Repeater"
    }
}
```

### Routing Rules

The same talkgroup lists control both **inbound** and **outbound** routing (symmetric):

- **slot1_talkgroups**: TGIDs allowed on timeslot 1
  - Traffic on TS1 FROM this repeater is only processed if TGID is in this list
  - Traffic on TS1 is only forwarded TO this repeater if TGID is in this list
  
- **slot2_talkgroups**: TGIDs allowed on timeslot 2
  - Traffic on TS2 FROM this repeater is only processed if TGID is in this list
  - Traffic on TS2 is only forwarded TO this repeater if TGID is in this list

**Symmetric routing ensures bidirectional communication** - if a repeater can send a talkgroup to the network, it can receive that talkgroup from the network.

### Forwarding Assumption

**Forwarding is always enabled** - that's the whole point of HBlink4! If a repeater has a TGID in its slot list, it will both accept traffic on that TS/TGID and receive forwarded traffic for that TS/TGID.

### Default Behavior

If a repeater configuration does not include slot talkgroup lists (empty `[]`):
- **Both directions**: All talkgroups are accepted and forwarded (no filtering)

This allows new repeaters to participate fully in the network without explicit configuration.

## Assumed Slot State

Since HBlink4 forwards calls to repeaters but doesn't receive real-time feedback about transmission state, we must **assume** the slot state on target repeaters:

### Transmission Assumptions

When we forward a stream to a repeater:
1. **Assume the slot is now active** - Track this as an "assumed active" stream
2. **Block the slot for other traffic** - Don't forward other calls to this TS until clear
3. **Honor hang time** - Keep slot blocked for `stream_hang_time` after stream ends
4. **Track stream lifecycle** - Monitor for terminators to know when transmission ends

### Slot State Tracking

For each repeater, we track:
- **Real streams**: Streams originating from this repeater (we receive packets)
- **Assumed streams**: Streams we're forwarding to this repeater (we send packets)

Both types of streams:
- Block the slot from other traffic
- Respect hang time after completion
- Use terminator detection for immediate end recognition
- Fall back to timeout if terminator is missed

## Contention Handling

When a call needs to be forwarded:

1. **Check inbound filter**: Does source repeater config allow this TS/TGID?
   - If no: Drop packet, don't process
   
2. **For each potential target repeater**:
   - Check outbound filter: Does target config include this TS/TGID?
   - If no: Skip this target
   
3. **Check target slot state**:
   - Is there an active stream (real or assumed) on this slot?
   - If yes: Check if it's the same call or different
     - Same call: Forward (continue existing stream)
     - Different call: Skip (contention - slot busy)
   
4. **Check hang time**:
   - Has this slot recently ended a stream?
   - If within hang time: Skip (slot cooling down)
   
5. **Forward packet**:
   - Send to target repeater
   - Create assumed stream state
   - Track for terminator/timeout

## Configuration Examples

### Simple Statewide Repeater
```json
{
    "name": "Statewide Repeater",
    "match": {"ids": [312000]},
    "config": {
        "enabled": true,
        "timeout": 30,
        "passphrase": "passw0rd",
        "slot1_talkgroups": [8],
        "slot2_talkgroups": [3120],
        "description": "Statewide traffic on TS2"
    }
}
```

### Multi-Purpose Repeater
```json
{
    "name": "Multi-Purpose Repeater",
    "match": {"ids": [312001]},
    "config": {
        "enabled": true,
        "timeout": 30,
        "passphrase": "passw0rd",
        "slot1_talkgroups": [8, 9],
        "slot2_talkgroups": [3120, 3122],
        "description": "Local on TS1, Regional/Statewide on TS2"
    }
}
```

### Single-Slot Operation
```json
{
    "name": "TS1 Only Repeater",
    "match": {"ids": [312002]},
    "config": {
        "enabled": true,
        "timeout": 30,
        "passphrase": "passw0rd",
        "slot1_talkgroups": [8, 9, 3120],
        "slot2_talkgroups": [],
        "description": "All traffic on TS1, TS2 disabled"
    }
}
```

### Same TGID on Both Slots
```json
{
    "name": "Dual-Slot Same TG",
    "match": {"ids": [312003]},
    "config": {
        "enabled": true,
        "timeout": 30,
        "passphrase": "passw0rd",
        "slot1_talkgroups": [3120],
        "slot2_talkgroups": [3120],
        "description": "Statewide TG available on both slots"
    }
}
```

## Implementation Notes

### Phase 3 Implementation

Current implementation status:
- ✅ Configuration parsing for slot-specific talkgroup lists
- ✅ Inbound filtering (accept/reject incoming calls based on TS/TGID)
- ✅ Outbound filtering (select forwarding targets based on TS/TGID)
- ✅ Assumed slot state tracking (track streams we're forwarding)
- ✅ Contention detection (prevent multiple streams on same slot)
- ✅ Hang time enforcement (respect hang time after stream ends)

### Simplicity Over Flexibility

HBlink4 uses a simplified routing model:
- **No separate inbound/outbound rules** - same lists control both directions
- **No wildcards** - explicit TGID lists only
- **No rewriting** - TS/TGID never changed during forwarding
- **Forwarding always enabled** - the whole purpose of HBlink4

This keeps configuration simple and predictable while covering the vast majority of real-world use cases.
