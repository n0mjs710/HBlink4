# HBlink4 Stream Tracking System

## Overview

The stream tracking system manages active DMR transmissions across all connected repeaters, handling per-slot per-repeater transmission state. This is a critical component that must be in place before implementing traffic forwarding/bridging.

## Design Principles

1. **Slot Independence**: Each repeater has two independent timeslots (1 and 2) that can carry different streams simultaneously
2. **First-Come-First-Served**: When multiple sources attempt to use the same slot, the first stream wins
3. **Talkgroup Filtering**: Only allowed talkgroups can be transmitted on a repeater
4. **Automatic Cleanup**: Stale streams are automatically removed after timeout (2 seconds)
5. **No Forwarding Yet**: This implementation tracks streams but does not forward traffic between repeaters

## Core Data Structures

### StreamState

Tracks an active DMR transmission stream on a specific repeater slot.

```python
@dataclass
class StreamState:
    radio_id: bytes          # Repeater this stream is on
    rf_src: bytes            # RF source (3 bytes)
    dst_id: bytes            # Destination talkgroup/ID (3 bytes)
    slot: int                # Timeslot (1 or 2)
    start_time: float        # When transmission started
    last_seen: float         # Last packet received
    stream_id: bytes         # Unique stream identifier (4 bytes)
    packet_count: int        # Number of packets in this stream
```

**Key Methods:**
- `is_active(timeout: float)`: Returns True if stream has received a packet within timeout period

### RepeaterState Extensions

Added to the existing `RepeaterState` dataclass:

```python
slot1_stream: Optional[StreamState] = None
slot2_stream: Optional[StreamState] = None
```

**Key Methods:**
- `get_slot_stream(slot: int)`: Get the active stream for a slot (1 or 2)
- `set_slot_stream(slot: int, stream: Optional[StreamState])`: Set or clear a slot's stream

## Stream Lifecycle

### 1. Stream Start

When a DMRD packet arrives on a slot with no active stream:

1. **Talkgroup Validation**: Check if the destination talkgroup is in the repeater's allowed list
2. **Stream Creation**: Create a new `StreamState` with initial packet count of 1
3. **Stream Assignment**: Assign to the appropriate slot (slot1_stream or slot2_stream)
4. **Logging**: Log stream start with source, destination, and stream_id

### 2. Stream Continuation

When a DMRD packet arrives on a slot with an active stream:

1. **Stream ID Match**: Verify packet's stream_id matches the active stream
2. **Update State**: Update `last_seen` timestamp and increment `packet_count`
3. **Terminator Check**: Check if packet is a DMR terminator frame
4. **Allow Forwarding**: Return True to indicate packet is valid (forwarding not yet implemented)

### 3. Stream Termination (Primary Detection)

When a DMR terminator frame is detected:

1. **Terminator Detection**: Check frame_type and sync pattern in packet
2. **Immediate End**: Mark stream as ended (ended=True) immediately
3. **Start Hang Time**: Begin hang time countdown to reserve slot
4. **Log Termination**: Log stream end with duration, packet count, and hang time period
5. **Slot Reservation**: Slot reserved for same rf_src only during hang time

### 4. Stream Contention

When a DMRD packet arrives with a different stream_id than the active stream:

1. **Contention Detected**: Two different sources trying to use the same slot
2. **Deny New Stream**: Reject the new stream (first-come-first-served)
3. **Log Warning**: Log the contention with both stream details
4. **Drop Packet**: Return False to drop the packet silently

### 5. Stream Timeout (Fallback Detection)

Checked every second by `_check_stream_timeouts()`:

1. **Timeout Check**: If no packet received in 2 seconds, stream is considered ended
2. **Mark as Ended**: Set ended=True, enter hang time (fallback for lost terminators)
3. **Hang Time Expiry**: After hang time period, clear slot stream
4. **Summary Logging**: Log stream duration and total packet count
5. **Slot Release**: Slot is now available for new streams from any source

## Assumed Streams and RX/TX Contention

### Overview

When we forward traffic TO a repeater (TX), we create an "assumed stream" to track what we're sending. However, repeaters have their own local users and may start receiving (RX) at any time. This creates a potential contention scenario.

### The Problem

**Scenario:**
1. We're transmitting to Repeater X slot 1 (TG 1) - assumed stream created
2. Repeater X starts receiving from a local user on slot 1 (TG 2) - real stream at virtually the same time
3. The repeater will ignore our TX packets (hardware busy receiving)
4. We waste bandwidth sending packets the repeater can't process

### The Solution: Route-Cache Removal

When a repeater starts receiving (real stream) while we have an assumed stream to it:

1. **Detection**: Check if current slot stream has `is_assumed=True`
2. **Route-Cache Removal**: Remove this repeater from ALL active streams' `target_repeaters` caches
3. **Stop Transmission**: We immediately stop sending to that repeater
4. **Allow Real Stream**: Clear assumed stream and process real stream normally
5. **Bandwidth Saved**: No wasted packets to busy repeater

### Implementation in `_handle_stream_start()`

```python
if current_stream:
    # Same stream continuing
    if current_stream.stream_id == stream_id:
        return True
    
    # Special case: Assumed stream (we're TX'ing) vs real stream (repeater RX'ing)
    if current_stream.is_assumed:
        LOGGER.info(f'Repeater {repeater_id} slot {slot} starting RX '
                   f'while we have assumed TX stream - repeater wins')
        
        # Remove from all active route-caches
        for other_repeater in self._repeaters.values():
            for other_slot in [1, 2]:
                other_stream = other_repeater.get_slot_stream(other_slot)
                if (other_stream and other_stream.routing_cached and 
                    other_stream.target_repeaters and
                    repeater.repeater_id in other_stream.target_repeaters):
                    other_stream.target_repeaters.discard(repeater.repeater_id)
        
        # Clear assumed stream, fall through to create real stream
    # ... rest of contention logic ...
```

### Performance

**Efficiency:** O(R×S) where R = repeaters, S = 2 slots
- Typical: ~10-20 operations when contention detected
- Only runs when repeater starts RX with assumed stream present
- Uses set `discard()` for O(1) removal per cache

**Benefit:**
- Immediate bandwidth savings
- No wasted packets to busy repeater
- Real streams always take precedence over assumed streams

### Real vs Assumed Streams

**Real Stream (`is_assumed=False`):**
- Received FROM a repeater (RX)
- Represents actual RF activity
- Always takes precedence
- Created in `_handle_stream_start()`

**Assumed Stream (`is_assumed=True`):**
- Sent TO a repeater (TX)
- Represents what we're forwarding
- Can be overridden by real streams
- Created in `_track_assumed_stream()`

### Logging

```
INFO - Repeater 312100 slot 1 starting RX while we have assumed TX stream - repeater wins, removing from active route-caches
DEBUG - Removed repeater 312100 from route-cache of stream on repeater 312101 slot 1
```

### Design Rationale

**Why repeater wins:**
- Repeaters have their own local users and hang time management
- We can't control when local users key up
- Repeater hardware can't TX and RX simultaneously on same slot
- Repeater will ignore our TX packets anyway when receiving

**Why remove from route-cache:**
- Stop wasting bandwidth immediately
- Efficient O(1) removal per cache using set operations
- Only affects streams targeting this specific repeater
- Automatic - no manual intervention needed

### Testing

Test coverage in `test_routing_optimization.py`:
- `test_assumed_stream_route_cache_removal()` validates the removal logic
- Verifies repeater is removed from route-caches
- Confirms real stream replaces assumed stream
- Validates bandwidth savings

## Key Methods

### `_is_dmr_terminator(data: bytes, frame_type: int) -> bool`

Checks if a DMR packet contains a stream terminator frame.

**Parameters:**
- `data`: Raw packet data
- `frame_type`: Extracted frame type from _bits field (0=voice, 1=voice sync, 2=data sync)

**Returns:**
- `True`: This is a terminator frame (stream ends immediately)
- `False`: Normal packet or terminator detection not yet implemented

**Logic:**
1. Check if frame_type is Voice Sync (0x01) or Data Sync (0x02)
2. Decode sync pattern from data[20:53] payload
3. Compare against known terminator patterns
4. Return True if terminator pattern found

**Note**: Currently returns False (stub implementation). When implemented, will enable immediate stream end detection (~60ms after PTT release) instead of waiting for timeout.

### `_is_talkgroup_allowed(repeater: RepeaterState, dst_id: bytes) -> bool`

Checks if a talkgroup is allowed on a repeater based on its configuration pattern match.

**Logic:**
1. Get repeater's configuration using `RepeaterMatcher`
2. Convert dst_id to integer
3. Check if talkgroup is in the `talkgroups` list from config
4. Return True if allowed, False otherwise

### `_handle_stream_start(repeater, rf_src, dst_id, slot, stream_id) -> bool`

Handles the start of a new stream on a repeater slot.

**Returns:**
- `True`: Stream can proceed
- `False`: Stream denied (contention or talkgroup not allowed)

**Logic:**
1. Check for existing stream on slot
   - If same stream_id: Allow (continuation)
   - If different stream_id: Deny (contention)
2. Check talkgroup permissions
3. Create new StreamState
4. Assign to slot
5. Log stream start

### `_handle_stream_packet(repeater, rf_src, dst_id, slot, stream_id) -> bool`

Handles any DMR packet, determining if it's a new stream or continuation.

**Returns:**
- `True`: Packet is valid and accepted
- `False`: Packet rejected (contention)

**Logic:**
1. Get current stream for slot
2. If no stream: Call `_handle_stream_start()`
3. If stream exists: Verify stream_id matches
4. Update stream state (last_seen, packet_count)
5. Return validity status

### `_check_stream_timeouts()`

Periodic check (every 1 second) to clean up stale streams.

**Logic:**
1. Iterate all connected repeaters
2. Check both slots for active streams
3. For each active stream:
   - Call `stream.is_active(2.0)` for 2-second timeout
   - If timed out: Log summary and clear slot
4. Allows slots to be reused after transmission ends

## Integration with DMR Data Handling

Updated `_handle_dmr_data()` method:

```python
def _handle_dmr_data(self, data: bytes, addr: PeerAddress) -> None:
    # ... validation code ...
    
    # Extract packet information including stream_id
    _stream_id = data[16:20]
    
    # Handle stream tracking
    stream_valid = self._handle_stream_packet(repeater, _rf_src, _dst_id, _slot, _stream_id)
    
    if not stream_valid:
        # Drop packet - contention or not allowed
        return
    
    # Packet accepted - ready for forwarding (not yet implemented)
    # TODO: Forward to other repeaters based on bridging rules
```

## Configuration Impact

Stream tracking respects the repeater configuration patterns:

**From config.json:**
```json
{
    "repeater_configurations": {
        "patterns": [
            {
                "name": "KS-DMR Network",
                "match": {"id_ranges": [[312000, 312099]]},
                "config": {
                    "talkgroups": [3120, 3121, 3122]
                }
            }
        ]
    }
}
```

**Effect:**
- Repeater 312050 can only transmit talkgroups 3120, 3121, 3122
- Packets for talkgroup 9 would be rejected on this repeater
- Packets for talkgroup 3120 would be accepted

## Logging

### Stream Start
```
INFO - Stream started on repeater 312100 slot 1: src=3121234, dst=3120, stream_id=a1b2c3d4
```

### Stream Timeout
```
INFO - Stream timeout on repeater 312100 slot 1: src=3121234, dst=3120, duration=4.52s, packets=226
```

### Contention
```
WARNING - Stream contention on repeater 312100 slot 1: existing stream (src=3121234, dst=3120) vs new stream (src=3125678, dst=3121)
```

### Talkgroup Denied
```
WARNING - Talkgroup 9 not allowed on repeater 312100 slot 1
```

### Packet Dropped
```
DEBUG - Dropped packet from repeater 312100 slot 1: src=3125678, dst=9, reason=stream contention or talkgroup not allowed
```

## Performance Considerations

1. **Memory Usage**: One StreamState object per active transmission (max 2 per repeater)
2. **CPU Usage**: Stream timeout check runs every 1 second (very lightweight)
3. **Scalability**: O(n) where n = number of connected repeaters (typically < 100)
4. **No Locking Needed**: Twisted's event loop is single-threaded

## Future Enhancements

When implementing traffic forwarding:

1. **Bridge Detection**: Check if talkgroup has `"bridge": true` in config
2. **Target Selection**: Find other repeaters with same talkgroup in their allowed list
3. **Packet Forwarding**: Send DMRD packet to target repeaters
4. **Sequence Management**: May need to track sequence numbers per destination
5. **Stream Synchronization**: Ensure stream_id is preserved across repeaters

## Testing Recommendations

1. **Single Repeater**: Verify stream tracking on one repeater, both slots
2. **Multiple Repeaters**: Connect multiple repeaters, verify independent slot tracking
3. **Contention**: Attempt simultaneous transmissions on same slot
4. **Talkgroup Filtering**: Send disallowed talkgroups, verify rejection
5. **Timeout**: Start stream, wait 3+ seconds, verify cleanup
6. **Rapid Succession**: Multiple streams in quick succession on same slot

## Known Limitations

1. **No Forwarding**: Traffic is not yet forwarded between repeaters
2. **No Late Join**: Repeaters joining mid-stream don't receive earlier packets
3. **No Priority**: All streams have equal priority (first-come-first-served)
4. **Fixed Timeout**: 2-second timeout is hardcoded (could be made configurable)
5. **No Stream End Detection**: Relies on timeout rather than DMR end-of-stream markers

## Next Steps

To implement traffic forwarding:

1. Add `_get_bridge_targets()` method to find repeaters that should receive traffic
2. Implement `_forward_dmr_packet()` to send to target repeaters
3. Add stream state synchronization across repeaters
4. Implement proper DMR stream end handling
5. Add statistics tracking (packets forwarded, bandwidth, etc.)
