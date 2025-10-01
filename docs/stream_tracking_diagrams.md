## Packet Processing Flow

```
+----------------------------------------------------------------+
|                    DMRD Packet Received                        |
+----------------------------------------------------------------+
                             |
                             ▼
                    +----------------+
                    | Extract Fields |
                    | - radio_id     |
                    | - rf_src       |
                    | - dst_id       |
                    | - slot         |
                    | - stream_id    |
                    +--------+-------+
                             |
                             ▼
                    +----------------+
                    |   Validate     |
                    |   Repeater     |
                    +--------+-------+
                             |
                  +----------+----------+
                  |                     |
            Connected?              Not Connected
                  |                     |
                  ▼                     ▼
    +---------------------+     +----------+
    | _handle_stream_     |     |   Drop   |
    |    _packet()        |     |  Packet  |
    +----------+----------+     +----------+
               |
               ▼
    +---------------------+
    | Get Current Stream  |
    |   for Slot          |
    +----------+----------+
               |
    +----------+----------+
    |                     |
  Empty?              Has Stream
    |                     |
    ▼                     ▼
+---------+    +-----------------+
|  Start  |    | stream_id match?|
|   New   |    +--------+--------+
| Stream  |                      |
+----+----+    +--------+--------+
     |       Yes               No
     |         |                 |
     |         ▼                 ▼
     |  +-----------+    +------------+
     |  |  Update   |    | CONTENTION |
     |  |  Stream   |    |    Drop    |
     |  |  State    |    |   Packet   |
     |  +-----+-----+    +------------+
     |        |
     +--------+---------+
                        |
                        ▼
              +------------------+
              | Talkgroup        |
              | Allowed?         |
              +--------+---------+
                       |
          +------------+------------+
         Yes                       No
          |                         |
          ▼                         ▼
    +----------+            +----------+
    |  Accept  |            |   Drop   |
    |  Packet  |            |  Packet  |
    +-----+----+            +----------+
          |
          ▼
    +------------------+
    | Check if DMR     |
    | Terminator Frame |
    | (_is_dmr_        |
    |  terminator)     |
    +--------+---------+
             |
    +--------+--------+
    |                 |
  Terminator      Normal Packet
    |                 |
    ▼                 |
+--------------+      |
| End Stream   |      |
| immediately  |      |
| + Start Hang |      |
|   Time       |      |
+--------------+      |
                      ▼
            +------------------+
            | TODO: Forward    |
            | to other         |
            | repeaters        |
            +------------------+
```

## Stream State Machine

```
                    +----------------------+
                    |   Slot Available     |
                    |  (no active stream)  |
                    +----------+-----------+
                               |
                               | First Packet Arrives
                               | + Talkgroup Allowed
                               ▼
                    +----------------------+
                    |   Stream Active      |
                    |                      |
                    | - Accepting packets  |
                    | - Updating last_seen |
                    | - Counting packets   |
                    +----------+-----------+
                               |
              +----------------┼----------------+------------------+
              |                |                |                  |
    Same stream_id    Different stream_id  Terminator        No packets
    (continue)        (contention - deny)  detected          for 2 seconds
              |                |                |                  |
              ▼                ▼                ▼                  ▼
    +-----------------+ +--------------+ +---------------+  +------------+
    |  Update State   | | Drop Packet  | |  End Stream   |  |  Timeout   |
    |  + Keep Active  | | + Log Warning| |  + Hang Time  |  |  + Cleanup |
    +-----------------+ +--------------+ +-------+-------+  +-----+------+
              |                                   |               |
              |                                   +---------------+
              |                                           |
              |                                          ▼
              |                                +------------------+
              |                                |  HANG TIME       |
              |                                |  - ended=True    |
              |                                |  - Same rf_src OK|
              |                                |  - Others DENIED |
              |                                +----------+-------+
              |                                         |
              |                                         | hang_time
              |                                         | expires
              |                                         ▼
              +---------------------------------------->|
                                                        |
                                             +----------------------+
                                             |   Slot Available     |
                                             |  (ready for new      |
                                             |   transmission)      |
                                             +----------------------+
```

## Multi-Slot Operation per Repeater

```
+--------------------------------------------------------+
|                    Repeater 312100                     |
|                 (192.168.1.100:62031)                  |
+--------------------+-----------------------------------+
                     |
         +-----------+-----------+
         |                       |
         ▼                       ▼
+-----------------+     +-----------------+
|     Slot 1      |     |     Slot 2      |
|                 |     |                 |
| slot1_stream:   |     | slot2_stream:   |
|                 |     |                 |
| StreamState:    |     | StreamState:    |
|  rf_src: 312123 |     |  rf_src: 312456 |
|  dst_id: 3120   |     |  dst_id: 3121   |
|  stream_id: ... |     |  stream_id: ... |
|  packets: 145   |     |  packets: 89    |
|  age: 1.2s      |     |  age: 0.8s      |
+-----------------+     +-----------------+
         |                       |
         +-----------------------+
         |  Independent slots    |
         |  Different streams    |
         |  Different talkgroups |
         +-----------------------+
```

## Contention Scenario

```
Time: t=0
+-----------------------------------------------------+
| Repeater 312100 Slot 1                              |
| Status: IDLE                                        |
+-----------------------------------------------------+

Time: t=0.1s
+-----------------------------------------------------+
| Repeater 312100 Slot 1                              |
| Stream: rf_src=312123 -> dst=3120 [ACTIVE]          |
|         stream_id=AAAA                              |
+-----------------------------------------------------+

Time: t=0.5s (during active stream)
+-----------------------------------------------------+
| NEW PACKET ARRIVES:                                 |
|   rf_src=312456 -> dst=3121                         |
|   stream_id=BBBB                                    |
|                                                     |
| CONTENTION DETECTED!                                |
|   Active: 312123 -> 3120 (AAAA)                     |
|   New:    312456 -> 3121 (BBBB)                     |
|                                                     |
| RESULT: New packet DROPPED                          |
|         Active stream continues                     |
|         [!]  WARNING logged                         |
+-----------------------------------------------------+

Time: t=2.46s (terminator frame received)
+-----------------------------------------------------+
| Repeater 312100 Slot 1                              |
| DMR TERMINATOR DETECTED (frame_type=sync)           |
| Status: HANG TIME (ended=True)                      |
| Reserved for: 312123 (original source)              |
| Expires: t=12.46s (10.0s hang time)                 |
+-----------------------------------------------------+

Time: t=12.5s (after hang time expires)
+-----------------------------------------------------+
| Repeater 312100 Slot 1                              |
| Status: IDLE (hang time expired)                    |
| Ready for new transmission from any source          |
+-----------------------------------------------------+
```

## Talkgroup Filtering

```
Configuration for Repeater 312100:
+--------------------------------------+
| Allowed Talkgroups: [3120, 3121]     |
+--------------------------------------+

Packet Arrives:
+---------------------------------------------------------+
| rf_src=312123 -> dst=3120                               |
|                                                         |
| Check: Is 3120 in [3120, 3121]? [OK] YES                |
| Result: ACCEPT packet                                   |
+---------------------------------------------------------+

Another Packet Arrives:
+---------------------------------------------------------+
| rf_src=312123 -> dst=9                                  |
|                                                         |
| Check: Is 9 in [3120, 3121]? [NO] NO                    |
| Result: DROP packet (talkgroup not allowed)             |
|         [!]  WARNING logged                             |
+---------------------------------------------------------+
```

## DMR Terminator Detection (Primary Method)

```
When DMR packet is received:

+---------------------------------------------------------+
| 1. Extract frame_type from _bits field (byte 15)        |
|    - bits 4-5: 00=voice, 01=voice sync,                 |
|                10=data sync, 11=unused                  |
|                                                         |
| 2. Call _is_dmr_terminator(data, frame_type)            |
|    - Check if frame_type is sync (0x01 or 0x02)         |
|    - Decode sync pattern from data[20:53]               |
|    - Compare against terminator patterns                |
|                                                         |
| 3. If terminator detected:                              |
|    - Mark stream.ended = True IMMEDIATELY               |
|    - Start hang_time countdown                          |
|    - Log stream termination                             |
|    - Timing: ~60ms after PTT release                    |
+---------------------------------------------------------+

Example Log Output (Terminator):
+--------------------------------------------------------+
| INFO - DMR terminator received on repeater 312100      |
|        slot 1: src=312123, dst=3120,                   |
|        duration=2.46s, packets=41 -                    |
|        entering hang time (10.0s)                      |
+--------------------------------------------------------+
```

## Stream Timeout Detection (Fallback Method)

```
_check_stream_timeouts() runs every 1 second:

+---------------------------------------------------------+
| For each connected repeater:                            |
|   For each slot (1, 2):                                 |
|     If slot has stream:                                 |
|       If (current_time - stream.last_seen) > 2.0:       |
|         If NOT ended:                                   |
|           SET: stream.ended = True (enter hang time)    |
|           LOG: Stream timeout, entering hang time       |
|         Elif NOT in_hang_time:                          |
|           SET: slot_stream = None                       |
|           LOG: Hang time expired                        |
|           RESULT: Slot now available                    |
+---------------------------------------------------------+

Example Log Output (Timeout Fallback):
+---------------------------------------------------------+
| INFO - Stream timeout on repeater 312100 slot 1:        |
|        src=312123, dst=3120, duration=4.52s,            |
|        packets=226 - entering hang time (10.0s)         |
|        [Used when terminator packet was lost]           |
+---------------------------------------------------------+

Note: Terminator detection is PRIMARY method (immediate).
      Timeout is FALLBACK for lost packets only.
```

## Future: Traffic Forwarding (Not Yet Implemented)

```
When forwarding is implemented:

Source Repeater              Bridge Logic            Target Repeaters
+--------------+            +------------+           +--------------+
|  Repeater A  |            |            |           |  Repeater B  |
|              |   DMRD     |  Check:    |   DMRD    |              |
| Slot 1: ACTIVE+---------->|  - Bridge? |---------->| Slot 1: ?    |
| TG 3120      |            |  - Allowed?|           | TG 3120      |
|              |            |  - Slot?   |           |              |
+--------------+            +----+-------+           +--------------+
                                 |                   +--------------+
                                 |       DMRD        |  Repeater C  |
                                 +------------------>|              |
                                                     | Slot 1: ?    |
                                                     | TG 3120      |
                                                     |              |
                                                     +--------------+

Decision Process:
1. Is TG 3120 configured with "bridge": true? -> Check config
2. Which repeaters have TG 3120 in their allowed list? -> Find targets
3. For each target, is slot 1 available? -> Check contention
4. If available, create/update stream on target
5. Forward packet to target repeater
```
