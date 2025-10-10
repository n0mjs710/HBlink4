# HBlink4 Stream Tracking System

## Overview

The stream tracking system is the core of HBlink4's DMR traffic management, providing per-slot per-repeater transmission state tracking with sophisticated contention handling and intelligent routing. This system enables HBlink4 to correctly manage simultaneous transmissions, prevent slot hijacking, and efficiently forward traffic between repeaters.

**Key Capabilities:**
- **Fast Terminator Detection**: Streams end in ~60ms (primary method) vs 2000ms timeout (fallback)
- **Hang Time Protection**: Prevents slot hijacking between transmissions in a conversation
- **Per-Stream Routing Cache**: Calculate-once, forward-many (O(1) routing decisions)
- **RX/TX Contention Handling**: Automatic bandwidth optimization when repeaters start receiving
- **Bidirectional Routing**: Symmetric inbound/outbound talkgroup filtering

## Design Principles

### 1. Slot Independence
Each repeater has two independent timeslots (1 and 2) that can carry different streams simultaneously. A repeater can be receiving on slot 1 while transmitting on slot 2, or handling different talkgroups on each slot.

### 2. First-Come-First-Served with Hang Time Protection
When multiple sources attempt to use the same slot:
- **Active streams**: First stream wins, others are denied (contention)
- **Hang time period**: After stream ends, slot is reserved for the original source only
- **Hang time rules**:
  - Same user: Can continue on same TG or switch to different TG (fast TG switching)
  - Different user, same TG: Can join conversation (multi-user conversation)
  - Different user, different TG: **DENIED** (hijacking prevention)

### 3. Fast Terminator Detection
Primary stream ending method using DMR frame analysis:
- **Terminator frame detected**: Stream ends immediately (~60ms after PTT release)
- **Timeout fallback**: Used only when terminator packet is lost (~2000ms delay)
- **Result**: Near-instant slot availability for legitimate users

### 4. Intelligent Routing with Per-Stream Caches
Traffic forwarding uses per-stream routing caches:
- **Calculate once**: Routing decisions made at stream start
- **Forward many**: All packets use cached targets (O(1) forwarding)
- **Inbound/outbound filtering**: Symmetric talkgroup access control
- **Automatic optimization**: Repeaters removed from cache when they start receiving

### 5. Real vs Assumed Streams
- **Real streams (RX)**: Traffic received FROM a repeater (actual RF activity)
- **Assumed streams (TX)**: Traffic sent TO a repeater (what we're forwarding)
- **Priority**: Real streams always win over assumed streams (repeater RX > server TX)

## Core Data Structures

### StreamState

Tracks an active DMR transmission stream on a specific repeater slot.

```python
@dataclass
class StreamState:
    repeater_id: bytes              # Repeater this stream is on (4 bytes)
    rf_src: bytes                   # RF source DMR ID (3 bytes)
    dst_id: bytes                   # Destination talkgroup/ID (3 bytes)
    slot: int                       # Timeslot (1 or 2)
    start_time: float               # When transmission started (Unix timestamp)
    last_seen: float                # Last packet received (Unix timestamp)
    stream_id: bytes                # Unique stream identifier (4 bytes)
    packet_count: int = 0           # Number of packets in this stream
    ended: bool = False             # True when stream ended (in hang time)
    end_time: Optional[float] = None  # When stream ended (for hang time calculation)
    call_type: str = "unknown"      # "group", "private", "data", or "unknown"
    is_assumed: bool = False        # True if TX stream (forwarded TO repeater)
    target_repeaters: Optional[set] = None  # Cached set of repeater_ids for forwarding
    routing_cached: bool = False    # True once routing calculated
```

**Key Methods:**
- `is_active(timeout: float)`: Returns True if stream has received a packet within timeout period
- `is_in_hang_time(timeout: float, hang_time: float)`: Returns True if stream is in hang time period

**Stream Types:**
- **RX Stream** (`is_assumed=False`): Traffic received from a repeater's local users
- **TX Stream** (`is_assumed=True`): Traffic we're forwarding to a repeater (tracking what we send)

### RepeaterState Extensions

Stream tracking fields added to `RepeaterState`:

```python
# Talkgroup access control (stored as sets for O(1) lookup)
# None = allow all, empty set = deny all, non-empty set = allow only those TGs
slot1_talkgroups: Optional[set] = None
slot2_talkgroups: Optional[set] = None
rpto_received: bool = False  # True if repeater sent RPTO

# Active stream tracking per slot
slot1_stream: Optional[StreamState] = None
slot2_stream: Optional[StreamState] = None
```

**Key Methods:**
- `get_slot_stream(slot: int)`: Get the active stream for a slot (1 or 2)
- `set_slot_stream(slot: int, stream: Optional[StreamState])`: Set or clear a slot's stream

## Stream Lifecycle

### 1. Stream Start (RX from Repeater)

When the first DMRD packet arrives on an idle slot:

```python
def _handle_stream_start(repeater, rf_src, dst_id, slot, stream_id, call_type_bit) -> bool
```

**Process:**
1. **Check for existing stream**:
   - If same `stream_id`: Allow (continuation - should not happen, but handle gracefully)
   - If different `stream_id` and stream active: **DENY** (contention)
   - If different `stream_id` and stream in hang time: Check hang time rules

2. **Hang Time Rules** (if stream exists and ended):
   - **Same user** (`rf_src` matches): Allow (can continue or switch TG)
   - **Different user, same TG** (`dst_id` matches): Allow (join conversation)
   - **Different user, different TG**: **DENY** (hijacking prevention)

3. **Inbound Routing Check** (if slot available):
   - Call `_check_inbound_routing(repeater_id, slot, tgid)`
   - Verify TG is in repeater's allowed list for this slot
   - If denied: Log warning and return False

4. **Calculate Routing Targets** (once per stream):
   - Call `_calculate_stream_targets(...)` to find which repeaters should receive this traffic
   - Checks outbound routing rules for each potential target
   - Checks slot availability (no contention on target)
   - Returns set of repeater_ids that will receive ALL packets in this stream

5. **Create StreamState**:
   - Initialize with `is_assumed=False` (real RX stream)
   - Store routing targets in `target_repeaters`
   - Set `routing_cached=True`
   - Set `packet_count=1`

6. **Assign to slot**: `repeater.set_slot_stream(slot, new_stream)`

7. **Log stream start**: `INFO - RX stream started on repeater X slot Y: src=..., dst=..., stream_id=..., targets=N`

8. **Emit event**: `stream_start` event to dashboard

9. **Update user cache**: Record last known repeater for this DMR ID (for private call routing)

### 2. Stream Continuation

When a DMRD packet arrives on a slot with an active stream:

```python
def _handle_stream_packet(repeater, rf_src, dst_id, slot, stream_id, call_type_bit) -> bool
```

**Process:**
1. **Get current stream**: `current_stream = repeater.get_slot_stream(slot)`

2. **If no stream**: Call `_handle_stream_start()` (first packet)

3. **Stream ID validation**:
   - **Same `stream_id`**: Update stream state
   - **Different `stream_id`**: Check for fast terminator or contention

4. **Fast Terminator Detection** (if different `stream_id`):
   - Check if old stream is still active (< 200ms since last packet)
   - If > 200ms: Old stream terminated without terminator packet
   - Log fast terminator, call `_end_stream()`, allow new stream via `_handle_stream_start()`
   - If < 200ms: Real contention, deny new stream

5. **Update stream state** (if same stream):
   - `stream.last_seen = current_time`
   - `stream.packet_count += 1`

6. **Return True**: Packet is valid for forwarding

### 3. Stream Termination (Primary: Terminator Frame)

When a DMR terminator frame is detected:

```python
def _is_dmr_terminator(data: bytes, frame_type: int) -> bool
```

**Terminator Detection:**
- **Byte 15 (_bits)** bits 4-5: frame_type must be 0x2 (HBPF_DATA_SYNC)
- **Byte 15 (_bits)** bits 0-3: dtype_vseq must be 0x2 (HBPF_SLT_VTERM)
- **Result**: Frame type 0x2 AND dtype 0x2 = terminator frame

**When terminator detected**:
1. Call `_end_stream(stream, repeater_id, slot, current_time, 'terminator')`
2. **Unified ending logic**:
   - Set `stream.ended = True`
   - Set `stream.end_time = current_time`
   - Calculate duration: `duration = end_time - start_time`
   - Log stream end: `INFO - RX stream ended: ... duration=X.XXs, packets=N, entering hang time (10.0s)`
   - Emit `stream_end` event to dashboard
   - **Do NOT clear slot** - stream enters hang time

3. **Hang time begins**: Slot reserved for original `rf_src`

**Timing**: ~60ms after PTT release (near-instant slot availability)

### 4. Stream Termination (Fallback: Timeout)

Checked every 1 second by `_check_stream_timeouts()`:

**Process:**
1. For each connected repeater, check both slots
2. If stream exists and not active (> 2.0 seconds since last packet):
   - If not ended: Call `_end_stream(stream, repeater_id, slot, current_time, 'timeout')`
   - Enters hang time (same as terminator method)
3. If stream ended and hang time expired:
   - Clear slot: `repeater.set_slot_stream(slot, None)`
   - Log: `DEBUG - RX hang time completed on repeater X slot Y`
   - Emit `hang_time_expired` event
   - Slot now available for new streams from any source

**Timing**: ~2000ms after last packet (fallback for lost terminators only)

### 5. Fast Terminator Detection (200ms Rule)

When a new stream arrives with different `stream_id` while a stream is active:

**Check for stale stream:**
- `time_since_last_packet = current_time - current_stream.last_seen`
- If > 200ms: Old stream effectively terminated (no terminator packet received)
- If < 200ms: Real contention (deny new stream)

**If fast terminator detected:**
1. Log: `INFO - Fast terminator: stream on repeater X slot Y ended via inactivity (XXXms since last packet)`
2. Call `_end_stream(stream, repeater_id, slot, current_time, 'fast_terminator')`
3. Stream enters hang time
4. Call `_handle_stream_start()` to check if new stream allowed (hang time rules apply)

**Benefit**: Detects operator releasing PTT without transmitting terminator frame (quick key-ups)

### 6. Stream Contention

When a DMRD packet arrives with a different `stream_id` than the active stream:

**Scenarios:**

**A. Active stream (< 200ms since last packet):**
- **Result**: DENY new stream
- **Log**: `WARNING - Stream contention on repeater X slot Y: existing stream (...) vs new stream (...)`
- **Reason**: First-come-first-served - active transmission has priority

**B. Stale stream (> 200ms since last packet):**
- **Result**: Fast terminator detection
- **Action**: End old stream, evaluate new stream against hang time rules
- **Log**: Fast terminator log + hang time check

**C. Stream in hang time:**
- **Check hang time rules**:
  - Same `rf_src`: Allow (same user continuing or switching TG)
  - Different `rf_src`, same `dst_id`: Allow (different user joining same TG conversation)
  - Different `rf_src`, different `dst_id`: **DENY** (hijacking attempt)
- **Log**: Appropriate message based on rule matched

## Hang Time Protection

Hang time provides sophisticated protection against slot hijacking while allowing legitimate multi-user conversations and fast TG switching.

### Configuration

```json
{
    "global": {
        "stream_hang_time": 10.0  // Seconds (10-20 recommended)
    }
}
```

**Recommended values:**
- **10 seconds**: Fast-paced networks, experienced operators
- **15 seconds**: Balanced (default)
- **20 seconds**: Slower operators, roundtable conversations

### Hang Time Rules

| Scenario | rf_src Match | dst_id Match | Result | Use Case |
|----------|--------------|--------------|--------|----------|
| **Same user, same TG** | ✓ Yes | ✓ Yes | ✅ ALLOW | User continuing conversation |
| **Same user, different TG** | ✓ Yes | ✗ No | ✅ ALLOW | Fast TG switching |
| **Different user, same TG** | ✗ No | ✓ Yes | ✅ ALLOW | Multi-user conversation |
| **Different user, different TG** | ✗ No | ✗ No | ❌ DENY | **Hijacking prevention** |

### Hang Time Protection Scenarios

#### Scenario 1: Same User Continuing Conversation

```
Timeline:
t=0s    : User 312123 transmits on TG 3120
t=2.5s  : User 312123 releases PTT (terminator detected)
t=2.5s  : Hang time begins (10.0s), slot reserved for 312123
t=4.0s  : User 312123 keys up again on TG 3120
Result  : ✅ ALLOWED - Same user, same TG

Log: "Same user continuing conversation during hang time: src=312123, dst=3120"
```

#### Scenario 2: Fast Talkgroup Switching

```
Timeline:
t=0s    : User 312123 transmits on TG 3120
t=2.5s  : User 312123 releases PTT (terminator detected)
t=2.5s  : Hang time begins (10.0s), slot reserved for 312123
t=3.0s  : User 312123 keys up on TG 9 (different talkgroup!)
Result  : ✅ ALLOWED - Same user can switch TGs

Log: "Same user switching talkgroup during hang time: src=312123, old_dst=3120, new_dst=9"
```

**Note**: This enables operators to quickly switch between talkgroups without waiting for hang time to expire.

#### Scenario 3: Multi-User Conversation (Roundtable)

```
Timeline:
t=0s    : User 312123 transmits on TG 3120
t=2.5s  : User 312123 releases PTT (terminator detected)
t=2.5s  : Hang time begins (10.0s), slot reserved for 312123
t=4.0s  : User 312456 keys up on TG 3120 (different user, SAME TG!)
Result  : ✅ ALLOWED - Different user joining conversation

Log: "Different user joining conversation during hang time: old_src=312123, new_src=312456, dst=3120"
```

**Benefit**: Allows natural roundtable conversations without hang time interference.

#### Scenario 4: Hijacking Prevention

```
Timeline:
t=0s    : User 312123 transmits on TG 3120
t=2.5s  : User 312123 releases PTT (terminator detected)
t=2.5s  : Hang time begins (10.0s), slot reserved for 312123
t=4.0s  : User 312456 tries to transmit on TG 9 (different user, different TG!)
Result  : ❌ DENIED - Hijacking attempt blocked

Log: "Hang time hijacking blocked: slot reserved for TG 3120, denied src=312456 attempting TG 9"
```

**This is the key protection**: Prevents users from stealing slots mid-conversation.

### Complex Scenario: Partial Talkgroup Overlap (A-B-C Network)

**Network topology:**
- Repeater A: TGs [3120, 9]
- Repeater B: TGs [3120, 3121] (shares 3120 with A, shares 3121 with C)
- Repeater C: TGs [3121, 8]

**Note**: Repeaters A and C do NOT share any talkgroups (isolated by B).

```
Scenario: A transmits TG 3120, B forwards to A, then C tries TG 3121

Timeline:
---------
t=0s:  User on Repeater A transmits TG 3120
       → Server receives from A (RX stream on A slot 1)
       → Server calculates targets: B has TG 3120
       → Server forwards to B (assumed TX stream on B slot 1)
       
       Repeater A Slot 1: RX stream (src=312123, dst=3120)
       Repeater B Slot 1: TX assumed stream (forwarded from A)
       Repeater C Slot 1: IDLE (doesn't have TG 3120)

t=2.5s: User on A releases PTT (terminator detected)
        → A enters hang time (reserved for user 312123, TG 3120)
        → B enters hang time (assumed stream ended)
        
        Repeater A Slot 1: HANG TIME (reserved for 312123 on TG 3120)
        Repeater B Slot 1: HANG TIME (assumed, lower priority)
        Repeater C Slot 1: IDLE

t=3.0s: User on Repeater C tries to transmit TG 3121
        → Server receives from C (RX stream attempt)
        → Server calculates targets: B has TG 3121 ✓
        → Server checks B slot 1 availability
        → B slot 1 is in hang time (assumed stream, TG 3120)
        → Assumed streams have LOW priority (real RX > assumed TX)
        → C's real RX wins over B's assumed TX
        → Server clears B's assumed stream
        → Server forwards C's TG 3121 to B
        
        Repeater A Slot 1: HANG TIME (still reserved for 312123)
        Repeater B Slot 1: RX from C (TG 3121) ✅ ALLOWED
        Repeater C Slot 1: RX stream (src=312789, dst=3121)

Result: ✅ C successfully uses B slot 1 because:
       1. A and C don't share TGs (isolated)
       2. B's slot 1 only had assumed stream (low priority)
       3. Real RX always wins over assumed TX
```

**Key Points:**
- Hang time on A protects A's slot from hijacking
- Hang time on B (assumed) doesn't block C (real RX wins)
- No TG overlap between A and C prevents interference
- B acts as isolated bridge between A and C networks

## Routing and Traffic Forwarding

### Per-Stream Routing Cache

HBlink4 uses a "calculate-once, forward-many" approach:

**At stream start** (`_calculate_stream_targets`):
1. Extract talkgroup from `dst_id`
2. For each connected repeater (except source):
   - Check outbound routing: Does target have this TG in allowed list?
   - Check slot availability: Is target slot idle or only in assumed hang time?
   - If both checks pass: Add to `target_repeaters` set
3. Store set in `StreamState.target_repeaters`
4. Set `StreamState.routing_cached = True`

**For every packet** (`_forward_stream`):
1. Check if `stream.routing_cached` is True
2. If yes: Use `stream.target_repeaters` set (O(1) lookup)
3. For each target in set: Send packet
4. **No recalculation needed** - routing decisions persist for entire stream

**Benefits:**
- **Performance**: O(1) forwarding per packet (set iteration)
- **Consistency**: All packets in stream follow same route
- **Scalability**: Efficient with 100+ repeaters
- **Simplicity**: No per-packet routing logic

### Inbound vs Outbound Routing

**Symmetric routing**: Same TG lists control both directions.

**Inbound (FROM repeater):**
```python
def _check_inbound_routing(repeater_id: bytes, slot: int, tgid: int) -> bool:
    allowed_tgids = repeater.slot1_talkgroups (or slot2_talkgroups)
    return tgid in allowed_tgids  # O(1) set membership
```

- Called when packet ARRIVES from repeater
- Checks if repeater is ALLOWED TO SEND this TG
- If denied: Drop packet, log warning

**Outbound (TO repeater):**
```python
def _check_outbound_routing(repeater_id: bytes, slot: int, tgid: int) -> bool:
    allowed_tgids = repeater.slot1_talkgroups (or slot2_talkgroups)
    return tgid in allowed_tgids  # O(1) set membership
```

- Called during `_calculate_stream_targets`
- Checks if repeater is ALLOWED TO RECEIVE this TG
- If denied: Exclude from target set

**Talkgroup Filtering Modes:**

| Config | Value | Behavior |
|--------|-------|----------|
| Not configured | N/A | Allow ALL talkgroups (legacy mode) |
| `slot1_talkgroups: null` | `None` | Allow ALL talkgroups |
| `slot1_talkgroups: []` | `[]` (empty) | **DENY ALL** (slot disabled) |
| `slot1_talkgroups: [1,2,3]` | Set `{1,2,3}` | Allow ONLY listed TGs |

## RX/TX Contention: Assumed Stream Handling

### The Problem

When server forwards traffic TO a repeater (TX), it creates an "assumed stream" to track what it's sending. However, repeaters have local users who can key up at any time, creating RX traffic. This creates a contention scenario.

**Without proper handling:**
- Server continues sending packets to busy repeater
- Repeater hardware ignores packets (can't TX and RX simultaneously)
- Wasted bandwidth (potentially significant on multi-repeater networks)

### The Solution

When a repeater starts receiving (RX) while we have an assumed stream (TX) to it:

**Detection**:
```python
if current_stream and current_stream.is_assumed and not current_stream.ended:
    # Repeater starting RX while we have active assumed TX
```

**Route-Cache Removal**:
```python
# Remove this repeater from ALL active streams' target_repeaters
for other_repeater in self._repeaters.values():
    for other_slot in [1, 2]:
        other_stream = other_repeater.get_slot_stream(other_slot)
        if other_stream and other_stream.routing_cached:
            if repeater.repeater_id in other_stream.target_repeaters:
                other_stream.target_repeaters.discard(repeater.repeater_id)
```

**Result**:
- Immediate bandwidth savings (no more packets to this repeater)
- Real RX stream processed normally
- Automatic - no configuration needed

**Performance**: O(R×S) where R = repeaters (~10-50), S = slots (2)
- Typical: 20-100 operations when contention detected
- Frequency: Rare (only when repeater starts RX with assumed TX present)
- Method: `set.discard()` is O(1) per removal

### Assumed Stream Priority Rules

| Current Stream | New Stream | Result | Reason |
|----------------|------------|--------|---------|
| Real RX | Real RX | Contention check | Normal rules apply |
| Real RX | Assumed TX | Don't create | Slot busy with real traffic |
| Assumed TX | Real RX | **Real wins** | Remove from route-cache |
| Assumed TX | Assumed TX | Update | Track multiple forwarding targets |

**Key principle**: Real (RX) always wins over Assumed (TX)

### Logging

```
INFO - Repeater 312100 slot 1 starting RX while we have active assumed TX stream - repeater wins, removing from active route-caches
DEBUG - Removed repeater 312100 from route-cache of stream on repeater 312101 slot 1
INFO - RX stream started on repeater 312100 slot 1: src=312567, dst=3121, stream_id=abc123, targets=3
```

## Private Call Routing

HBlink4 uses a user cache to enable efficient private call routing.

**User Cache:**
- Tracks last known repeater for each DMR ID
- Updated on every transmission (stream start)
- Timeout: 600 seconds (configurable, minimum 60)
- Cleanup: Every 60 seconds (removes expired entries)

**Private Call Process:**
1. Extract `dst_id` from packet
2. Check `call_type` bit: 0 = private, 1 = group
3. If private call:
   - Look up `dst_id` in user cache
   - If found: Route to that specific repeater only
   - If not found: Drop packet (user not seen recently)
4. If group call:
   - Use normal talkgroup routing (all repeaters with this TG)

**Configuration:**
```json
{
    "global": {
        "user_cache": {
            "timeout": 600  // Seconds (minimum 60)
        }
    }
}
```

## Performance Characteristics

### Memory Usage

**Per Repeater:**
- RepeaterState: ~2KB
- StreamState (max 2): ~400 bytes each
- Total: ~2.8KB per repeater

**100 Repeaters**: ~280KB (negligible)

### CPU Usage

**Per-Packet Operations:**
- Repeater lookup: O(1) dict lookup
- Stream validation: O(1) stream_id comparison
- Routing decision: O(1) set membership (cached targets)
- Forwarding: O(T) where T = targets in cache (typically 3-10)

**Periodic Tasks:**
- Stream timeout check: Every 1 second, O(R×2) where R = repeaters
- User cache cleanup: Every 60 seconds, O(U) where U = cached users
- Forwarding stats: Every 5 seconds, O(1)

**Scalability**: Linear O(n) with repeater count, sub-millisecond per-packet processing

### Network Bandwidth

**Without Route-Cache Optimization:**
- Worst case: Forwarding to 50 repeaters with 10 active streams
- Wasted bandwidth: ~100KB/s per busy repeater

**With Route-Cache Optimization:**
- Assumed streams removed immediately when repeater starts RX
- Bandwidth saved: Up to 100KB/s per optimized repeater
- Detection latency: < 60ms (first RX packet)

## Configuration Impact

Stream tracking respects repeater configuration patterns and talkgroup access control.

**Example Configuration:**
```json
{
    "repeater_configurations": {
        "patterns": [
            {
                "name": "KS-DMR Network",
                "match": {"id_ranges": [[312000, 312099]]},
                "config": {
                    "passphrase": "secret",
                    "slot1_talkgroups": [8, 9],
                    "slot2_talkgroups": [3120, 3121, 3122]
                }
            }
        ],
        "default": {
            "passphrase": "default-pass",
            "slot1_talkgroups": [8],
            "slot2_talkgroups": [8]
        }
    }
}
```

**Effect on Stream Tracking:**

**Repeater 312050** (matches KS-DMR pattern):
- Slot 1: Can RX and TX TGs 8, 9 only
- Slot 2: Can RX and TX TGs 3120, 3121, 3122 only
- Packet for TG 1 on slot 1: **DENIED** (inbound routing check fails)
- Forward TG 3120 to this repeater slot 2: **ALLOWED** (outbound routing check passes)

**Repeater 999999** (matches default):
- Slot 1: Can RX and TX TG 8 only
- Slot 2: Can RX and TX TG 8 only
- Very restricted access (guest/untrusted repeater pattern)

## Logging

HBlink4 provides comprehensive logging of stream activity:

### Stream Start (RX)
```
INFO - RX stream started on repeater 312100 slot 1: src=3121234, dst=3120, stream_id=a1b2c3d4, targets=3
```

### Stream Start (TX Assumed)
```
DEBUG - TX stream created on repeater 312100 slot 1: dst=3120 (assumed, forwarding)
```

### Stream Termination (Terminator Detected)
```
INFO - RX stream ended on repeater 312100 slot 1: src=3121234, dst=3120, duration=2.46s, packets=41, entering hang time (10.0s)
```

### Stream Termination (Timeout Fallback)
```
INFO - RX stream ended on repeater 312100 slot 1: src=3121234, dst=3120, duration=4.52s, packets=226, entering hang time (10.0s)
```

### Fast Terminator Detection
```
INFO - Fast terminator: stream on repeater 312100 slot 1 ended via inactivity (215ms since last packet): src=3121234, dst=3120, duration=1.85s, packets=31
```

### Hang Time Expiry
```
DEBUG - RX hang time completed on repeater 312100 slot 1: src=3121234, dst=3120, hang_duration=10.02s
```

### Contention (Active Stream)
```
WARNING - Stream contention on repeater 312100 slot 1: existing stream (src=3121234, dst=3120, active 150ms ago) vs new stream (src=3125678, dst=3121)
```

### Hang Time Protection (Same User Continuing)
```
INFO - Same user continuing conversation on repeater 312100 slot 1 during hang time: src=3121234, dst=3120
```

### Hang Time Protection (Fast TG Switch)
```
INFO - Same user switching talkgroup on repeater 312100 slot 1 during hang time: src=3121234, old_dst=3120, new_dst=9
```

### Hang Time Protection (Different User, Same TG)
```
INFO - Different user joining conversation on repeater 312100 slot 1 during hang time: old_src=3121234, new_src=3125678, dst=3120
```

### Hang Time Protection (Hijacking Blocked)
```
WARNING - Hang time hijacking blocked on repeater 312100 slot 1: slot reserved for TG 3120, denied src=3125678 attempting TG 9
```

### Inbound Routing Denied
```
WARNING - Inbound routing denied: repeater=312100 TS1/TG9 not in allowed list {8, 3120}
```

### RX/TX Contention (Assumed Stream Override)
```
INFO - Repeater 312100 slot 1 starting RX while we have active assumed TX stream - repeater wins, removing from active route-caches
DEBUG - Removed repeater 312100 from route-cache of stream on repeater 312101 slot 1
```

## Event Emission (Dashboard Integration)

Stream tracking emits real-time events to the dashboard:

### stream_start
```json
{
    "repeater_id": 312100,
    "slot": 1,
    "src_id": 3121234,
    "dst_id": 3120,
    "stream_id": "a1b2c3d4",
    "call_type": "group"
}
```

### stream_end
```json
{
    "repeater_id": 312100,
    "slot": 1,
    "src_id": 3121234,
    "dst_id": 3120,
    "duration": 2.46,
    "packets": 41,
    "end_reason": "terminator",
    "hang_time": 10.0,
    "call_type": "group",
    "is_assumed": false
}
```

### hang_time_expired
```json
{
    "repeater_id": 312100,
    "slot": 1
}
```

**Dashboard Usage:**
- Real-time repeater slot status display
- Recent events log (filters out TX assumed streams)
- Traffic statistics and analytics
- Network health monitoring

## What Sets HBlink4 Apart

HBlink4's stream tracking system provides capabilities that surpass other DMR server implementations:

### 1. Fast Terminator Detection
- **HBlink4**: ~60ms stream end detection (primary method)
- **Others**: ~2000ms timeout-only detection
- **Benefit**: Near-instant slot availability, better user experience

### 2. Sophisticated Hang Time Protection
- **HBlink4**: 4 distinct rules (same user, user switch TG, join conversation, hijacking prevention)
- **Others**: Simple timeout or no protection
- **Benefit**: Prevents hijacking while allowing natural conversations and fast TG switching

### 3. Per-Stream Routing Cache
- **HBlink4**: Calculate-once, forward-many with O(1) decisions
- **Others**: Per-packet routing calculations
- **Benefit**: Massively improved performance on large networks

### 4. RX/TX Contention Handling
- **HBlink4**: Automatic route-cache removal, bandwidth optimization
- **Others**: Continue sending to busy repeaters
- **Benefit**: Significant bandwidth savings on multi-repeater networks

### 5. Real vs Assumed Stream Priority
- **HBlink4**: Real RX always wins over assumed TX
- **Others**: May not distinguish or handle poorly
- **Benefit**: Correct behavior when repeaters start receiving

These capabilities enable HBlink4 to scale to large networks (100+ repeaters) while maintaining excellent performance and correct DMR behavior in complex contention scenarios.
