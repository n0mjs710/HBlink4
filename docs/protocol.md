# HomeBrew DMR Protocol Specification

This document describes the HomeBrew DMR protocol used for communication between DMR repeaters and servers *as implemented by this application*. There are extensions to HBP that are not used, and thus not documented. The primary source for information on HBP should be the MMDVMHost and DMRGateway code. While that code is poorly commented and undocumented, it must still be considered primary. The protocol is based on UDP packets and implements a quasi connection-oriented approach with authentication and keepalive mechanisms.

> **Important Note**: The protocol specification at wiki.brandmeister.network contains errors in the keepalive/ping mechanism. Specifically, it reverses the command prefixes, incorrectly stating that MSTPING is sent from repeater to server and RPTPONG is sent back. The correct flow is: repeater sends RPTPING and server responds with MSTPONG. This document provides the correct implementation.

## Protocol Overview

The HomeBrew DMR protocol uses a series of commands exchanged between repeaters and servers to establish and maintain connections, authenticate devices, and transfer DMR data.

HBlink4 speaks both sides of this protocol:

- As a **server**, it accepts inbound repeater connections (the bulk of this document).
- As a **client**, it makes outbound connections to other HomeBrew servers, where it behaves as a repeater. See [HBlink4 as a Client](#hblink4-as-a-client-outbound-connections).

OpenBridge (server-to-server peering) is a different protocol and is documented separately in [openbridge.md](openbridge.md).

## Important DMR Specifications

- **Repeater (peer) IDs are 32-bit (4-byte) fields.** Every command that carries a repeater ID uses exactly 4 bytes, including any necessary leading zeros.
- **Subscriber and talkgroup IDs inside DMRD are 24-bit (3-byte) fields.** The `rf_src` and `dst_id` fields of a DMRD packet are 3 bytes, matching the DMR over-the-air protocol. The `SRC=` option override is likewise validated against the 24-bit range.
- Do not conflate the two: a repeater ID on the wire is 4 bytes, a radio or talkgroup ID inside a voice packet is 3.

## Connection States

A server tracks each repeater in one of the following states:

- `login` - Initial state after a login request is received; a salt has been sent and the server is waiting for the authentication response
- `config` - Authentication completed, waiting for configuration
- `connected` - Fully connected and operational

A fourth, transient condition exists that is not a state of a registered repeater: a **pending login**, held aside when a login claims a radio ID that is already registered from a different address. See [ID Takeover](#id-takeover-login-for-an-already-registered-id).

## Command Types

### Connection Establishment

1. **RPTL (Repeater Login)**
   - Direction: Repeater → Server
   - Purpose: Initial login request
   - Format: `RPTL` + `repeater_id[4 bytes]`
   - Server Response: `RPTACK` + `salt[4 bytes]` (the authentication challenge)
   - State Change: (none) → `login`

2. **RPTACK + salt (Authentication Challenge)**
   - Direction: Server → Repeater
   - Format: `RPTACK` + `salt[4 bytes]`
   - Purpose: Issue the authentication challenge in response to RPTL
   - The salt is a random 32-bit value generated per login
   - A login retry from the same address re-sends the **same** salt rather than
     generating a new one, so a repeater that retries mid-handshake is not
     invalidated by a changing challenge
   - Note: This packet is **not** MSTCL. MSTCL is only sent when the server is
     shutting down (see below).
   - Note: `RPTACK` + salt and `RPTACK` + repeater_id are both 10 bytes and are
     indistinguishable by length alone. A client tells them apart by
     handshake position: the first RPTACK received after RPTL carries the salt;
     every RPTACK after the RPTK has been sent is a plain acknowledgment.

3. **MSTNAK (Server NAK)**
   - Direction: Server → Repeater
   - Format: `MSTNAK` + `repeater_id[4 bytes]`
   - Purpose: Reject a request or indicate an error
   - Used when: repeater not registered, packet from an incorrect source address,
     invalid state transitions, authentication failures, no matching
     configuration, ID reserved for an outbound connection, ping timeout, or a
     registration displaced by an authenticated takeover

4. **RPTK (Repeater Authentication)**
   - Direction: Repeater → Server
   - Format: `RPTK` + `repeater_id[4 bytes]` + `auth_hash[32 bytes]`
   - Purpose: Respond to the authentication challenge
   - `auth_hash` = raw SHA-256 digest of `salt[4 bytes, big-endian] || passphrase`,
     where the passphrase is the UTF-8 encoded shared secret
   - Server Response: `RPTACK` + `repeater_id[4 bytes]` on success, `MSTNAK` on failure
   - On failure the repeater is removed and must start over from RPTL
   - State Change: `login` → `config`

5. **RPTC (Repeater Configuration)**
   - Direction: Repeater → Server
   - Format: `RPTC` + `repeater_id[4 bytes]` + `configuration_data`
   - Purpose: Send repeater configuration
   - Server Response: `RPTACK` + `repeater_id[4 bytes]`
   - State Change: `config` → `connected`
   - Total Length: 302 bytes

   Configuration Data Fields (all fields are fixed length):
   | Field         | Offset | Length | Description                  |
   |---------------|--------|--------|------------------------------|
   | Command       | 0      | 4      | 'RPTC'                       |
   | Repeater ID   | 4      | 4      | 32-bit DMR ID                |
   | Callsign      | 8      | 8      | Station callsign             |
   | RX Frequency  | 16     | 9      | Receive frequency            |
   | TX Frequency  | 25     | 9      | Transmit frequency           |
   | TX Power      | 34     | 2      | Transmit power               |
   | Color Code    | 36     | 2      | DMR color code               |
   | Latitude      | 38     | 8      | Station latitude             |
   | Longitude     | 46     | 9      | Station longitude            |
   | Height        | 55     | 3      | Antenna height               |
   | Location      | 58     | 20     | Station location description |
   | Description   | 78     | 19     | Station description          |
   | Slots         | 97     | 1      | Enabled timeslots            |
   | URL           | 98     | 124    | Station URL                  |
   | Software ID   | 222    | 40     | Software identifier          |
   | Package ID    | 262    | 40     | Package identifier           |

   Note: All string fields are fixed length and should be null-padded if shorter than their allocated length.

   HBlink4 uses Software ID and Package ID to derive a descriptive category
   (`hotspot`, `repeater`, `network`, or `unknown`); Package ID is consulted
   first and Software ID is the fallback. These strings are client-supplied,
   forgeable metadata, not hardware identity.

   With BuenoDMR Strict Hotspot Access enabled, the server requires this RPTC
   to be exactly 302 bytes and the derived category to be `hotspot` before it
   acknowledges configuration and marks the session `connected`. Unknown and
   other categories are rejected. DMRD is accepted only from connected
   sessions. See [DMR access checks and origin limits](radio-access-security.md).

6. **RPTCL (Repeater Close)**
   - Direction: Repeater → Server
   - Format: `RPTCL` + `repeater_id[4 bytes]`
   - Purpose: Graceful connection termination
   - The server removes the registration immediately and sends no reply
   - Note: `RPTCL` shares its first four bytes with `RPTC`, so a receiver must
     check five bytes to tell a close from a configuration packet.

7. **MSTCL (Server Close)**
   - Direction: Server → Repeater
   - Format: `MSTCL` (no repeater ID)
   - Purpose: Tell connected repeaters the server is shutting down
   - Sent to every repeater in the `connected` state during graceful shutdown.
     It is **not** part of the login handshake.

### Repeater Options

**RPTO (Repeater Options)**
   - Direction: Repeater → Server
   - Format: `RPTO` + `repeater_id[4 bytes]` + `options_string`
   - Purpose: Send talkgroup configuration for each timeslot
   - Server Response: `RPTACK` + `repeater_id[4 bytes]` — sent even when parsing
     fails, so a malformed options string does not cause the repeater to retry
     forever
   - The options string may be null-padded; trailing NULs are stripped on receipt
   - Requires a registered repeater whose source address matches its
     registration. In practice repeaters send RPTO once the connection is
     established, but the server does not reject one that arrives earlier.
   - Can be sent at any time after connection is established; each RPTO fully
     replaces the previous options (there is no incremental update)

   **Options String Format (basic):**
   ```
   TS1=tg1,tg2,tg3;TS2=tg4,tg5,tg6
   ```

   **Behavior:**
   - Repeater sends list of desired talkgroups for TS1 and TS2
   - For **untrusted** repeaters, the server intersects the request with the
     configured allowed talkgroups: only talkgroups present in both the repeater
     request AND the server config are accepted, and the config is the master
     allow list
   - For **trusted** repeaters (`trust` set on the matched pattern), the
     requested talkgroups are taken as-is; talkgroups beyond the configured list
     are logged but honored
   - A slot that is **not mentioned** falls back to the configured default for
     that slot
   - `TS1=*` (wildcard) means "not specified" — the configured default applies
   - An **empty** value (`TS1=`) means deny-all on that slot, which is why it is
     distinct from omitting the slot entirely

   **Example:**
   ```
   RPTO + [repeater_id] + "TS1=1,2,3,91;TS2=10,99"
   ```

   If server config allows: TS1=[1,2,3,4,5] and TS2=[10,20,30]
   Result (untrusted repeater): TS1=[1,2,3] (91 rejected), TS2=[10] (99 rejected)

   **Extended Options Grammar (trusted repeaters only):**

   HBlink4 accepts an extended form of each comma-separated entry to declare
   slot/talkgroup translation, plus top-level `SRC=` and `UNIT=` directives:

   ```
   entry          = net_tgid[:local_slot[:local_tgid]]
   net_tgid       = N | N-M           (range; inclusive; ≤10,000 tgids per entry)
   local_slot     = 1 | 2 | *          (* = preserve network slot)
   local_tgid     = N | *              (* = preserve matched network tgid)

   SRC  = radio_id                     (outbound rf_src override, group voice only)
   UNIT = true | false                 (participate in unit/private call routing)
   ```

   - `SRC=` rewrites the rf_src on every group-voice packet forwarded out of this
     repeater. One-way, group only, and the value must fit in 24 bits.
   - `UNIT=` accepts `true/1/yes/on` and `false/0/no/off`. When absent, the
     pattern's default applies; like talkgroups and `SRC=`, it is a full
     replacement rather than a sticky setting.
   - Wildcards are **not** permitted on the net side (no `*`, no `N*` prefix).
   - Most-specific rule wins on collision (exact=3 > range=2); the less-specific
     rule is dropped with a warning.

   **Trust gating:** translation entries, `SRC=`, and `UNIT=` are honored only
   for repeaters whose matched pattern sets `trust`. An untrusted repeater that
   sends any of them gets a warning in the log and has those directives ignored —
   its plain talkgroup subscription is still processed normally.

   See [dmrd_translation.md](dmrd_translation.md) for semantics, Link Control
   rewriting behavior, and use cases, and [connecting_to_hblink4.md](connecting_to_hblink4.md)
   for operator-facing `Options=` examples.

### Connection Maintenance

1. **RPTPING (Repeater Ping)**
   - Direction: Repeater → Server
   - Format: `RPTPING` + `repeater_id[4 bytes]`
   - Purpose: Repeater keepalive message
   - Notes:
     - Sent periodically by the repeater
     - Server responds with MSTPONG + repeater_id
     - Updates last_ping timestamp
     - Resets missed_pings counter
     - Requires the repeater to be in the `connected` state; a ping in any other
       state is answered with MSTNAK
     - Note: While repeaters always send 'RPTPING', only 'RPTP' is needed to identify the command when parsing,
       as these are the only significant characters needed to disambiguate it from other commands

2. **MSTPONG (Server Ping Response)**
   - Direction: Server → Repeater
   - Format: `MSTPONG` + `repeater_id[4 bytes]`
   - Purpose: Acknowledge keepalive message from repeater
   - Usage:
     - Server sends in response to RPTPING/RPTP
     - Confirms server received keepalive message

3. **RPTACK (Server Acknowledgment)**
   - Direction: Server → Repeater
   - Format: `RPTACK` + `repeater_id[4 bytes]`
   - Purpose: General acknowledgment for non-ping messages
   - Usage:
     - Sent in response to RPTK, RPTC, RPTO, and DMRA (never to pings)
     - Confirms the server received and accepted the message
     - See also `RPTACK` + salt above, which is the same command used as the
       login challenge

4. **Timeout Behavior**
   - Repeaters are expected to ping well within the configured `timeout_duration`
     (default 30 seconds)
   - **Any** packet arriving from a repeater's registered source address
     refreshes its liveness, not only RPTPING. A packet bearing a known repeater
     ID from any *other* address does not, so a stale or spoofed source cannot
     hold a dead registration open.
   - The server sweeps for timeouts on an interval and increments a missed-ping
     counter for each repeater whose last packet is older than `timeout_duration`
   - After `max_missed` increments (default 3), the server considers the repeater
     disconnected, sends MSTNAK, and removes it from active connections. Because
     the counter advances once per sweep, the wall-clock time to disconnect is
     roughly `max_missed × sweep interval`, not `timeout_duration`.
   - Repeater must re-register from RPTL if the connection is lost

### Data Transfer

1. **DMRD (DMR Data)**
   - Direction: Bidirectional
   - Format: `DMRD` + `sequence[1 byte]` + `rf_src[3 bytes]` + `dst_id[3 bytes]` + `repeater_id[4 bytes]` + `_bits[1 byte]` + `stream_id[4 bytes]` + `payload[33 bytes]`
   - Purpose: Transfer DMR voice/data packets
   - Notes: Only processed when connection state is 'connected'
   - Structure Length: 53 bytes (4 + 1 + 3 + 3 + 4 + 1 + 4 + 33)
   - **Wire Length: 55 bytes.** HBP repeater sources (MMDVMHost and friends)
     append two trailing bytes (BER and RSSI) to the 53-byte structure. HBlink4
     **rejects any DMRD shorter than 55 bytes** and ignores the trailing bytes
     when parsing. Paths that require a canonical frame — OpenBridge egress, for
     one — truncate back to exactly 53 bytes.

   **DMRD Packet Structure:**

   | Field       | Offset | Length | Description                       |
   |-------------|--------|--------|-----------------------------------|
   | Command     | 0      | 4      | 'DMRD'                            |
   | Sequence    | 4      | 1      | Packet sequence number (0-255)    |
   | RF Source   | 5      | 3      | Source radio ID (24-bit)          |
   | Destination | 8      | 3      | Destination talkgroup/ID (24-bit) |
   | Repeater ID | 11     | 4      | Repeater ID (32-bit)              |
   | _bits       | 15     | 1      | Control bits (see below)          |
   | Stream ID   | 16     | 4      | Unique stream identifier          |
   | Payload     | 20     | 33     | DMR voice/data payload            |
   | (BER/RSSI)  | 53     | 2      | Appended by HBP sources; ignored  |

   **_bits Field (byte 15):**
   - Bit 7: Timeslot (0=Slot 1, 1=Slot 2)
   - Bit 6: Call Type (0=Group, 1=Private/Unit)
     - This bit encodes DMR *addressing* only. It does not distinguish voice from
       data — that distinction lives in the frame type and dtype_vseq below.
   - Bits 4-5: Frame Type
     - `00` - Voice frame
     - `01` - Voice Sync (header/terminator)
     - `10` - Data Sync (header/terminator)
     - `11` - Unused/reserved. HBlink4 does not reject it; anything that is not
       Data Sync is classified as voice.
   - Bits 0-3: Data Type / Voice Sequence (dtype_vseq)
     - For Data Sync frames (`10`), the value names a DMR data type:
       `0x0`=PI-Header, `0x1`=VHEAD (voice header with full LC), `0x2`=VTERM
       (voice terminator with full LC), `0x3`=CSBK, `0x4`=MBC-Header,
       `0x5`=MBC-Continuation, `0x6`=Data-Header, `0x7`=Rate-1/2 Data,
       `0x8`=Rate-3/4 Data, `0x9`=Idle, `0xA`=Rate-1 Data,
       `0xB`=Unified-Single-Block, `0xC`=Unified-Data-Continuation
     - For Voice / Voice Sync frames: `0x0`=burst A, `0x1`..`0x4`=bursts B..E (each carrying a 32-bit EMB_LC fragment), `0x5`=burst F

   **Voice vs. Data Streams:**

   A stream is classified on its first packet. A Data Sync frame with
   dtype_vseq VHEAD (`0x1`) or VTERM (`0x2`) starts a voice call; any other
   Data Sync dtype_vseq starts a data call (CSBK, data headers, data PDUs).
   Voice and Voice Sync frames are voice, which also lets a late-entry stream
   that arrives mid-superframe without a VHEAD be tracked correctly.

   Data calls are tracked and reported to the dashboard but are **never
   forwarded**.

   **DMR Stream Terminator Detection:**

   In the Homebrew protocol, terminator frames are indicated in byte 15:
   - Bits 4-5 (frame type): Must be 0x2 (HBPF_DATA_SYNC)
   - Bits 0-3 (dtype_vseq): Must be 0x2 (HBPF_SLT_VTERM)

   ```python
   def is_dmr_terminator(data: bytes, frame_type: int) -> bool:
       if len(data) < 16:
           return False
       _bits = data[15]
       _dtype_vseq = _bits & 0xF
       return frame_type == 2 and _dtype_vseq == 2
   ```

   This provides ~60ms detection (3x faster than timeout methods).

   **Note**: ETSI sync patterns (bytes 20-25) are not used for terminator detection,
   as the Homebrew protocol provides explicit flags in the packet header.

2. **DMRA (DMR Talker Alias)**
   - Direction: Repeater → Server
   - Format: `DMRA` + `repeater_id[4 bytes]` + `talker_alias_data`
   - Purpose: Carry DMR Talker Alias blocks (typically callsign/name)
   - Server Response: `RPTACK` + `repeater_id[4 bytes]`
   - HBlink4 acknowledges DMRA and logs it, but does not yet parse or forward
     the alias data. The ACK is sent even on a processing error so the repeater
     does not retry.

## Connection Flow

1. **Initial Connection**
   ```
   Repeater                        Server
      |                              |
      |----------- RPTL ------------>| (Login Request)
      |                              |
      |<------ RPTACK + salt --------| (Authentication Challenge)
      |                              |
      |----------- RPTK ------------>| (Authentication Response)
      |                              |
      |<---- RPTACK + repeater_id ---| (Authentication Accepted)
      |                              |
      |----------- RPTC ------------>| (Configuration)
      |                              |
      |<---- RPTACK + repeater_id ---| (Configuration Accepted — now connected)
      |                              |
      |----------- RPTO ------------>| (Options — optional)
      |                              |
      |<---- RPTACK + repeater_id ---|
   ```

   Any step may instead be answered with `MSTNAK` + repeater_id, which ends the
   attempt; the repeater must restart from RPTL.

2. **Keepalive Flow**
   ```
   Repeater                   Server
      |                         |
      |-------- RPTPING ------->|
      |<------- MSTPONG --------|
      |                         |
   ```

3. **Shutdown**
   ```
   Repeater                   Server
      |                         |
      |<-------- MSTCL ---------| (Server shutting down; no repeater ID)

   Repeater                   Server
      |                         |
      |-------- RPTCL --------->| (Repeater disconnecting; no reply)
   ```

### ID Takeover (Login for an Already-Registered ID)

A login that claims a repeater ID already registered from a *different* source
address is not trusted to be the incumbent returning on a new NAT port. It must
prove the passphrase before it may replace the live registration:

```
Claimant                   Server                    Incumbent
   |                         |                            |
   |--------- RPTL --------->|                            |
   |                         | (held as a pending login;  |
   |                         |  incumbent untouched)      |
   |<---- RPTACK + salt -----|                            |
   |                         |                            |
   |--------- RPTK --------->|                            |
   |<-- RPTACK + repeater_id-|                            |
   |                         | (claimant waits for RPTC;  |
   |                         |  incumbent still active)   |
   |--------- valid RPTC --->|                            |
   |                         |--- MSTNAK ---------------->| (after admission)
```

- The incumbent stays registered through authentication and remains active until
  the claimant supplies a structurally valid RPTC and passes the configured
  admission policy.
- On a wrong passphrase or no matching configuration, the claimant gets MSTNAK
  and the pending login is discarded; the incumbent keeps running.
- A malformed or disallowed RPTC is rejected and discarded without evicting the
  incumbent. If the incumbent has already timed out, a successful claimant is
  promoted only after its RPTC passes admission.
- An unanswered challenge is expired after 30 seconds.
- A login from the incumbent's own address is an ordinary reconnect and takes
  the normal path, re-sending the same salt while in the `login` state.
- Repeater IDs reserved for outbound connections are rejected outright: an
  inbound login for such an ID always gets MSTNAK.

## Timing and Reliability

- Repeaters must send RPTPING regularly to maintain the connection
- Server responds to each RPTPING with MSTPONG + repeater_id
- If a repeater misses multiple pings, the connection is considered dead, the
  server sends MSTNAK, and the registration is removed
- A repeater closing down cleanly should send RPTCL; a server closing down sends
  MSTCL to every connected repeater

## Error Handling

- Invalid packets or state transitions are responded to with MSTNAK
- Authentication failures result in connection termination: the server NAKs and
  removes the repeater, which must restart from RPTL
- Missing keepalive responses should trigger reconnection attempts
- Configuration errors should be logged and the connection reset
- Packets from a source address that does not match the registration are NAKed,
  never processed

## Security Considerations

- All repeaters must authenticate before sending data
- Repeater IDs must be validated
- A claim on an already-registered ID must be authenticated before it can
  displace the incumbent (see [ID Takeover](#id-takeover-login-for-an-already-registered-id))
- Configuration data should be validated before acceptance
- Connection states must be strictly enforced
- Implement rate limiting for login attempts

## HBlink4 as a Client (Outbound Connections)

For outbound connections, HBlink4 reverses roles and behaves as a repeater
against a remote HomeBrew server. The command set is the same; only the
direction changes.

Sends: `RPTL`, `RPTK`, `RPTC`, `RPTO`, `RPTPING`, `RPTCL`, `DMRD`
Handles: `RPTACK` (both forms), `MSTNAK`, `MSTPONG`, `MSTCL`, `DMRD`

Client-side behavior worth noting:

- The handshake is driven by RPTACK position, since `RPTACK` + salt and
  `RPTACK` + repeater_id cannot be told apart by length: the first RPTACK after
  RPTL is treated as the challenge, and each subsequent one advances the
  handshake (RPTK → RPTC → RPTO).
- RPTL is retried on the keepalive interval until authentication succeeds.
- RPTO is null-padded to 300 bytes on send. HBlink4's server side strips
  trailing NULs on receipt, so padded and unpadded options strings both work.
- MSTPONG resets a missed-pong counter; three missed pongs drop the connection
  and trigger a reconnect from RPTL.
- On shutdown or teardown, an authenticated outbound connection sends RPTCL.

## Implementation Notes

- All messages are UDP packets
- Repeater IDs are 4 bytes; radio and talkgroup IDs inside DMRD are 3 bytes
- String commands (like 'RPTL') are ASCII encoded
- Binary data (like DMR voice) is raw bytes
- Command matching must account for shared prefixes: `RPTC`/`RPTCL` need 5 bytes
  to disambiguate, while `RPTPING` can be identified from its `RPTP` prefix alone
- Implement appropriate timeouts for all states
- Log all protocol messages when debugging
