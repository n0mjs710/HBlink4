# Stream Hang Time Feature

## Overview

The **stream hang time** feature prevents talkgroup hijacking during multi-transmission conversations. When a DMR transmission ends, the timeslot remains reserved for the **same talkgroup** (or original user) for a configurable period, preventing other talkgroups from interrupting an ongoing conversation.

## The Problem

In DMR, a typical conversation consists of multiple separate transmissions on the same talkgroup:

```
Time: 0s    User A transmits on TG 3120 (stream 1)
Time: 3s    Stream 1 ends
Time: 3.5s  User B replies on TG 3120 (stream 2) - same conversation
```

Without hang time, a different talkgroup could hijack the slot:

```
Time: 0s    User A transmits TG 3120 (stream 1)
Time: 3s    Stream 1 ends, slot becomes available
Time: 3.1s  User C transmits TG 9999 (hijacks the slot!)
Time: 3.5s  User B tries to reply on TG 3120 - BLOCKED
```

## The Solution

Hang time reserves the slot for the **same talkgroup** (or original user) after transmission ends:

```
Time: 0s    User A transmits TG 3120 (stream 1)
Time: 3s    Stream 1 ends, slot enters HANG TIME for TG 3120
Time: 3.1s  User C tries TG 9999 - DENIED (slot reserved for TG 3120)
Time: 3.5s  User B transmits TG 3120 - ALLOWED (same talkgroup)
Time: 6.5s  User B's stream ends, hang time starts
Time: 9.5s  Hang time expires, slot available for any talkgroup
```

## Configuration

Add to the `global` section of your configuration file:

```json
{
    "global": {
        "stream_timeout": 2.0,
        "stream_hang_time": 10.0
    }
}
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stream_timeout` | float | 2.0 | Fallback timeout when DMR terminator frame is lost |
| `stream_hang_time` | float | 10.0-20.0 | Seconds to reserve slot for same **talkgroup** after stream ends |

### Recommended Values

- **stream_timeout**: 2.0 seconds (fallback only)
  - **Primary detection**: DMR terminator frame (immediate)
  - **Fallback detection**: Timeout after 2 seconds without packets
  - This timeout is only used when the terminator packet is lost
  - 2.0 seconds provides safety margin for worst-case packet loss
  - Too short: May trigger during temporary network congestion
  - Too long: Delays cleanup when terminator is actually lost
  
- **stream_hang_time**: 10.0-20.0 seconds (configurable for your network)
  - This is the **slot reservation period** to prevent **talkgroup hijacking**
  - Typical PTT release and re-key time: 0.5-2.0 seconds
  - 10.0 seconds: Good for active/fast-paced conversations
  - 20.0 seconds: Better for slower operators or tactical operations
  - Longer values improve conversation flow but reduce slot availability
  - Shorter values risk conversation interruption

### Stream End Detection Methods

**Primary: Immediate Terminator Detection (~60ms) ✅**
- Checks packet header flags in byte 15
- Frame type == 0x2 (HBPF_DATA_SYNC) AND dtype_vseq == 0x2 (HBPF_SLT_VTERM)
- Uses Homebrew protocol's built-in terminator flags
- Provides optimal ~60ms detection latency
- **3x faster than timeout-based methods**

**Fallback: Timeout Detection (2.0s inactivity)**
- Triggers after `stream_timeout` (2.0s default) with no packets
- Used when no new transmission attempts to take the slot
- Ensures streams eventually clean up even if operators don't key up again
- Checked every 1 second by background task

## How It Works

### Stream States

A stream progresses through three states:

1. **ACTIVE**: Receiving packets, `ended=False`
   - Packets from same stream_id: ACCEPTED
   - New stream from different TG: DENIED (slot busy)

2. **HANG TIME**: Stream ended (`ended=True`), slot reserved
   - Packets from SAME talkgroup: ACCEPTED (conversation continues)
   - Packets from SAME rf_src: ACCEPTED (original user can switch TGs)
   - Packets from DIFFERENT TG/source: DENIED (slot reserved)
   - Duration: `stream_hang_time` seconds

3. **EXPIRED**: Hang time elapsed
   - Slot cleared (set to None)
   - Available for any talkgroup

### State Transitions

```
+---------------+
|    ACTIVE     | <--- New packet, same stream_id
| ended=False   |
+-------+-------+
        |
        | Terminator or timeout
        v
+---------------+
|   HANG TIME   | <--- Same TG or same rf_src can use slot
| ended=True    |      Different TG/source DENIED
+-------+-------+
        |
        | stream_hang_time elapses
        v
+---------------+
|    EXPIRED    |
| slot=None     | <--- Any talkgroup can use slot
+---------------+
```

### DMR Packet Timing

Understanding DMR timing clarifies the detection behavior:

- **Voice Packet Rate**: ~60ms per packet (16.67 packets/second)
- **Typical Transmission**: 30-90 seconds (500-1500 packets)
- **Immediate Detection**: Terminator frame detected at end of transmission (~60ms)
- **Fallback**: 2.0s timeout if terminator packet is lost
- **hang_time**: Slot reservation to protect **talkgroup conversations**
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

### Different Talkgroup Denied

```
WARNING - Hang time contention on repeater 312100 slot 1: 
          slot reserved for TG=3120, denied TG=9999
```

### Hang Time Expires

```
DEBUG - Hang time expired on repeater 312100 slot 1
```

## Use Cases

### Normal Talkgroup Conversation

Multiple users on TG 3120:

```
0.0s:  User A starts TG 3120 (stream 1) → ACTIVE
2.5s:  User A stops, stream ends → HANG TIME (reserved for TG 3120)
3.0s:  User B transmits TG 3120 (stream 2) → ACTIVE (same TG allowed)
5.0s:  User B stops, stream ends → HANG TIME (reserved for TG 3120)
8.0s:  User C transmits TG 3120 (stream 3) → ACTIVE (same TG)
11.0s: User C stops, stream ends → HANG TIME
21.0s: Hang time expires → AVAILABLE
```

### Attempted Talkgroup Hijacking

TG 3120 active, TG 9999 tries to interrupt:

```
0.0s:  User A transmits TG 3120 (stream 1) → ACTIVE
2.5s:  User A stops, stream ends → HANG TIME (reserved for TG 3120)
2.8s:  User X transmits TG 9999 → DENIED (hang time protecting TG 3120)
3.0s:  User B transmits TG 3120 (stream 2) → ACTIVE (same TG)
```

### Slot Reuse After Conversation

Conversation ends, different talkgroup can use slot:

```
0.0s:  User A transmits TG 3120 (stream 1) → ACTIVE
2.5s:  User A stops, stream ends → HANG TIME (reserved for TG 3120)
12.5s: Hang time expires → AVAILABLE
13.0s: User X transmits TG 9999 (stream 2) → ACTIVE (slot free)
```

### Same User Switching Talkgroups

Original user can switch TGs during hang time:

```
0.0s:  User A (3121234) transmits TG 3120 → ACTIVE
2.5s:  Stream ends → HANG TIME (reserved for TG 3120 or User A)
3.0s:  User A (3121234) transmits TG 3121 → ACTIVE (same user, different TG allowed)
```

## Benefits

✅ **Prevents Talkgroup Hijacking**: Ongoing TG conversations are protected
✅ **Natural Operation**: Users don't notice the mechanism
✅ **Configurable**: Adjust for different operator speeds and network behavior
✅ **Per-Slot**: Each timeslot operates independently
✅ **Fair Access**: After conversation ends, slot is available to all
✅ **User Flexibility**: Original user can switch talkgroups during hang time

## Limitations

⚠️ **Slot Reservation**: Hang time does reserve the slot, reducing availability for other TGs
⚠️ **Single TG Protection**: Only protects one talkgroup at a time per slot
⚠️ **No Priority**: All talkgroups treated equally (first-come-first-served)

## Stream End Detection

HBlink4 uses a **two-tier detection system** for stream end:

### Tier 1: DMR Terminator Frame Detection (Primary)

The primary method detects explicit DMR terminator frames:

**Terminator Detection**:
- Checks packet header flags in byte 15
- Frame type == 0x2 (HBPF_DATA_SYNC) AND dtype_vseq == 0x2 (HBPF_SLT_VTERM)
- Uses Homebrew protocol's built-in terminator flags
- When detected: Immediately ends stream and starts hang time

**Benefits**:
- ✅ Immediate stream end (~60ms after PTT release)
- ✅ Fast slot turnaround for new conversations on same TG
- ✅ Accurate hang time start
- ✅ Better slot utilization
- ✅ 3x faster than timeout-based detection

### Tier 2: Timeout Detection (Fallback)

- Triggers after `stream_timeout` (2.0s default) with no packets
- Used when terminator frame is lost in transmission
- Ensures streams eventually clean up even without explicit terminators
- Checked every 1 second by background task

## Testing

Comprehensive tests in `tests/test_hang_time.py`:

- ✅ Active streams don't trigger hang time
- ✅ Ended streams enter hang time correctly
- ✅ Hang time expires after configured duration
- ✅ Same source can resume during hang time
- ✅ Different source is denied during hang time
- ✅ Edge cases (boundaries, zero hang time, etc.)
