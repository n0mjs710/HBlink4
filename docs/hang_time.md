# Stream Hang Time Feature

## Overview

The **stream hang time** feature prevents slot hijacking during multi-transmission conversations. When a DMR transmission ends, the timeslot remains reserved for the same RF source for a configurable period, preventing other stations from interrupting an ongoing conversation.

## The Problem

In DMR, a typical conversation consists of multiple separate transmissions:

```
Time: 0s    User A transmits (stream 1)
Time: 3s    Stream 1 ends
Time: 3.5s  User A transmits again (stream 2) - same conversation
```

Without hang time, this could happen:

```
Time: 0s    User A transmits (stream 1)
Time: 3s    Stream 1 ends, slot becomes available
Time: 3.1s  User B starts transmitting (hijacks the slot!)
Time: 3.5s  User A tries to continue - BLOCKED
```

## The Solution

Hang time reserves the slot for the original source after transmission ends:

```
Time: 0s    User A transmits (stream 1)
Time: 3s    Stream 1 ends, slot enters HANG TIME for User A
Time: 3.1s  User B tries to transmit - DENIED (slot reserved)
Time: 3.5s  User A transmits again - ALLOWED (same source)
Time: 6.5s  User A's stream ends, hang time starts
Time: 9.5s  Hang time expires, slot available for anyone
```

## Configuration

Add to the `global` section of your configuration file:

```json
{
    "global": {
        "stream_timeout": 2.0,
        "stream_hang_time": 3.0
    }
}
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stream_timeout` | float | 2.0 | Fallback timeout when DMR terminator frame is lost |
| `stream_hang_time` | float | 10.0-20.0 | Seconds to reserve slot for same source after stream ends |

### Recommended Values

- **stream_timeout**: 2.0 seconds (fallback only)
  - **Primary detection**: DMR terminator frame (immediate)
  - **Fallback detection**: Timeout after 2 seconds without packets
  - This timeout is only used when the terminator packet is lost
  - 2.0 seconds provides safety margin for worst-case packet loss
  - Too short: May trigger during temporary network congestion
  - Too long: Delays cleanup when terminator is actually lost
  
- **stream_hang_time**: 10.0-20.0 seconds (configurable for your network)
  - This is the **slot reservation period** to prevent hijacking
  - Typical PTT release and re-key time: 0.5-2.0 seconds
  - 10.0 seconds: Good for active/fast-paced conversations
  - 20.0 seconds: Better for slower operators or tactical operations
  - Longer values improve conversation flow but reduce slot availability
  - Shorter values risk conversation interruption

### Stream End Detection Methods

**Primary: Fast Terminator Detection (200ms inactivity)**
- When a new stream attempts to start on an occupied slot
- Check if current stream hasn't received packets for >200ms
- If so, end old stream immediately and allow new stream
- Provides ~200ms turnaround (vs ETSI's ~60ms goal)
- Works reliably without needing sync pattern decoding

**Fallback: Timeout Detection (2.0s inactivity)**
- Triggers after `stream_timeout` (2.0s default) with no packets
- Used when no new transmission attempts to take the slot
- Ensures streams eventually clean up even if operators don't key up again
- Checked every 1 second by background task

**Primary: Immediate Terminator Detection (~60ms) ✅**
- Checks packet header flags in byte 15
- Frame type == 0x2 (HBPF_DATA_SYNC) AND dtype_vseq == 0x2 (HBPF_SLT_VTERM)
- Uses Homebrew protocol's built-in terminator flags
- Provides optimal ~60ms detection latency
- **3x faster than timeout-based methods**

### DMR Packet Timing

Understanding DMR timing clarifies the detection behavior:

- **Voice Packet Rate**: ~60ms per packet (16.67 packets/second)
- **Typical Transmission**: 10-60 packets (0.6-3.6 seconds)
- **Immediate Detection**: Terminator frame detected at end of transmission (~60ms)
- **Fallback**: 2.0s timeout if terminator packet is lost
- **hang_time**: Slot reservation to protect the conversation

## How It Works

### Stream States

A stream progresses through three states:

1. **ACTIVE**: Receiving packets, `ended=False`
   - Packets from same stream_id: ACCEPTED
   - Packets from different stream/source: DENIED (contention)

2. **HANG TIME**: No packets for `stream_timeout`, `ended=True`
   - Packets from SAME rf_src: ACCEPTED (conversation continues)
   - Packets from DIFFERENT rf_src: DENIED (slot reserved)
   - Duration: `stream_hang_time` seconds

3. **EXPIRED**: Hang time elapsed
   - Slot cleared (set to None)
   - Available for any source

### State Transitions

```
+---------------+
|    ACTIVE     | <--- New packet, same stream_id
| ended=False   |
+-------+-------+
        |
        | No packets for stream_timeout
        v
+---------------+
|   HANG TIME   | <--- Same rf_src can resume
| ended=True    |      Different rf_src DENIED
+-------+-------+
        |
        | stream_hang_time elapses
        v
+---------------+
|    EXPIRED    |
| slot=None     | <--- Any source can use slot
+---------------+
```
## Logging

### Stream End via Terminator (Primary Method)

```
INFO - DMR terminator received on repeater 312100 slot 1: 
       src=3121234, dst=3120, duration=2.46s, packets=41 - 
       entering hang time (10.0s)
```

### Stream End via Timeout (Fallback Method)

```
INFO - Stream timeout on repeater 312100 slot 1: src=3121234, dst=3120, 
       duration=4.52s, packets=226 - entering hang time (10.0s)
```

### Same Source Resumes

```
INFO - Same source resuming on repeater 312100 slot 1 during hang time: 
       src=3121234, old_dst=3120, new_dst=3121
```

### Different Source Denied

```
WARNING - Hang time contention on repeater 312100 slot 1: 
          slot reserved for src=3121234, denied src=3125678
```

### Hang Time Expires

```
DEBUG - Hang time expired on repeater 312100 slot 1
```

## Use Cases

### Normal Conversation

User A having a conversation:

```
0.0s:  A starts (stream 1) → ACTIVE
2.5s:  A stops, stream ends → HANG TIME (reserved for A)
3.0s:  A resumes (stream 2) → ACTIVE (same source allowed)
5.0s:  A stops, stream ends → HANG TIME
8.0s:  Hang time expires → AVAILABLE
```

### Attempted Interruption

User A talking, User B tries to interrupt:

```
0.0s:  A starts (stream 1) → ACTIVE
2.5s:  A stops, stream ends → HANG TIME (reserved for A)
2.8s:  B tries to transmit → DENIED (hang time active)
3.0s:  A resumes (stream 2) → ACTIVE
```

### Slot Reuse After Conversation

Conversation ends, different user can transmit:

```
0.0s:  A starts (stream 1) → ACTIVE
2.5s:  A stops, stream ends → HANG TIME
5.5s:  Hang time expires → AVAILABLE
6.0s:  B starts (stream 3) → ACTIVE (slot free)
```

### Different Talkgroup Same Source

User A switching talkgroups mid-conversation:

```
0.0s:  A transmits TG 3120 → ACTIVE
2.5s:  Stream ends → HANG TIME (reserved for A)
3.0s:  A transmits TG 3121 → ACTIVE (same source, different TG)
```

## Benefits

✅ **Prevents Interruption**: Ongoing conversations are protected
✅ **Natural Operation**: Users don't notice the mechanism
✅ **Configurable**: Adjust for different operator speeds
✅ **Per-Slot**: Each timeslot operates independently
✅ **Fair Access**: After conversation ends, slot is available to all

## Limitations

⚠️ **Slot Reservation**: Hang time does reserve the slot, reducing availability
⚠️ **Single Source**: Only protects one RF source at a time per slot
⚠️ **No Priority**: All sources treated equally (first-come-first-served)

## Stream End Detection

HBlink4 uses a **two-tier detection system** for stream end:

### Tier 1: DMR Terminator Frame Detection (Primary)

The primary method detects explicit DMR terminator frames:

**DMR Frame Types** (in byte 15, bits 4-5):
- `00` - Voice frame
- `01` - Voice Sync (appears at start and end)
- `10` - Data Sync (appears at start and end)

**Terminator Detection**:
- Checks if frame_type is Voice Sync (0x01) or Data Sync (0x02)
- Checks packet header flags from byte 15
- Frame type and dtype_vseq indicate terminator
- When detected: Immediately ends stream and starts hang time

**Benefits**:
- ✅ Immediate stream end (~60ms after PTT release)
- ✅ Fast slot turnaround for new conversations
- ✅ Accurate hang time start
- ✅ Better slot utilization
- ✅ 3x faster than timeout-based detection

## Testing

Comprehensive tests in `tests/test_hang_time.py`:

- ✅ Active streams don't trigger hang time
- ✅ Ended streams enter hang time correctly
- ✅ Hang time expires after configured duration
- ✅ Same source can resume during hang time
- ✅ Different source is denied during hang time
- ✅ Edge cases (boundaries, zero hang time, etc.)
