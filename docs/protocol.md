# HomeBrew DMR Protocol Specification

This document describes the HomeBrew DMR protocol used for communication between DMR repeaters and servers. The protocol is based on UDP packets and implements a quasi connection-oriented approach with authentication and keepalive mechanisms.

> **Important Note**: The protocol specification at wiki.brandmeister.network contains errors in the keepalive/ping mechanism. Specifically, it reverses the command prefixes, incorrectly stating that MSTPING is sent from repeater to server and RPTPONG is sent back. The correct flow is: repeater sends RPTPING and server responds with MSTPONG. This document provides the correct implementation.

## Protocol Overview

The HomeBrew DMR protocol uses a series of commands exchanged between repeaters and servers to establish and maintain connections, authenticate devices, and transfer DMR data.

## Important DMR Specifications

- All Radio and Talkgroup IDs are 32-bit (4-byte) fields
- This matches the DMR over-the-air protocol where IDs map to IPv4 addresses
- Radio and Talkgroup IDs in all packets must be exactly 4 bytes, including any necessary leading zeros

## Connection States

A server will, at a minimum, need to track a repeater in the following states:
- `login` - Initial state after login request received
- `config` - Authentication completed, waiting for configuration
- `connected` - Fully connected and operational

## Command Types

### Connection Establishment

1. **RPTL (Repeater Login)**
   - Direction: Repeater → Server
   - Purpose: Initial login request
   - Format: `RPTL` + `radio_id[4 bytes]`
   - Note: Radio ID is always a 32-bit (4-byte) field as per DMR specification
   - State Change: `no` → `rptl-received`

2. **MSTNAK (Server NAK)**
   - Direction: Server → Repeater
   - Purpose: Reject a request/indicate an error
   - Used when: Invalid state transitions or authentication failures

3. **RPTK (Repeater Authentication)**
   - Direction: Repeater → Server
   - Format: `RPTK` + `radio_id[4 bytes]` + `authentication_response`
   - Purpose: Respond to authentication challenge
   - State Change: `rptl-received` → `waiting-config`

4. **RPTC (Repeater Configuration)**
   - Direction: Repeater → Server
   - Format: `RPTC` + `radio_id[4 bytes]` + `configuration_data`
   - Purpose: Send repeater configuration
   - State Change: `config` → `connected`
   - Total Length: 302 bytes
   
   Configuration Data Fields (all fields are fixed length):
   | Field         | Offset | Length | Description |
   |---------------|--------|--------|-------------|
   | Command       | 0      | 4      | 'RPTC' |
   | Radio ID      | 4      | 4      | 32-bit DMR ID |
   | Callsign      | 8      | 8      | Station callsign |
   | RX Frequency  | 16     | 9      | Receive frequency |
   | TX Frequency  | 25     | 9      | Transmit frequency |
   | TX Power      | 34     | 2      | Transmit power |
   | Color Code    | 36     | 2      | DMR color code (RF air interface only, not used by server) |
   | Latitude      | 38     | 8      | Station latitude |
   | Longitude     | 46     | 9      | Station longitude |
   | Height        | 55     | 3      | Antenna height |
   | Location      | 58     | 20     | Station location description |
   | Description   | 78     | 19     | Station description |
   | Slots         | 97     | 1      | Enabled timeslots |
   | URL           | 98     | 124    | Station URL |
   | Software ID   | 222    | 40     | Software identifier |
   | Package ID    | 262    | 40     | Package identifier |
   
   Note: All string fields are fixed length and should be null-padded if shorter than their allocated length.

5. **RPTCL (Repeater Close)**
   - Direction: Repeater → Server
   - Format: `RPTCL` + `radio_id[4 bytes]`
   - Purpose: Graceful connection termination

### Connection Maintenance

1. **RPTPING (Repeater Ping)**
   - Direction: Repeater → Server
   - Format: `RPTPING` + `radio_id[4 bytes]`
   - Purpose: Repeater keepalive message
   - Notes: 
     - Sent periodically by repeater
     - Server responds with MSTPONG + radio_id
     - Updates last_ping timestamp
     - Resets missed_pings counter
     - Note: While repeaters always send 'RPTPING', only 'RPTP' is needed to identify the command when parsing,
       as these are the only significant characters needed to disambiguate it from other commands

2. **MSTPONG (Server Ping Response)**
   - Direction: Server → Repeater
   - Format: `MSTPONG` + `radio_id[4 bytes]`
   - Purpose: Acknowledge keepalive message from repeater
   - Usage:
     - Server sends in response to RPTPING/RPTP
     - Confirms server received keepalive message

3. **Timeout Behavior**
   - Repeater must send RPTPING/RPTP within the configured timeout period (default 30s)
   - Server tracks missed pings from each repeater
   - After max_missed pings (default 3), server considers repeater disconnected
   - Server will send NAK and remove repeater from active connections
   - Repeater must re-register if connection is lost

3. **RPTACK (Server Response)**
   - Direction: Server → Repeater
   - Format: `RPTACK` + `radio_id[4 bytes]`
   - Purpose: General acknowledgment for non-ping messages
   - Usage:
     - Server sends in response to various messages (except pings)
     - Confirms server received and accepted message

### Data Transfer

1. **DMRD (DMR Data)**
   - Direction: Bidirectional
   - Format: `DMRD` + `sequence[1 byte]` + `rf_src[3 bytes]` + `dst_id[3 bytes]` + `radio_id[4 bytes]` + `_bits[1 byte]` + `stream_id[4 bytes]` + `payload[33 bytes]`
   - Purpose: Transfer DMR voice/data packets
   - Notes: Only processed when connection state is 'connected'
   - Total Length: 53 bytes (4 + 1 + 3 + 3 + 4 + 1 + 4 + 33)

   **DMRD Packet Structure:**
   
   | Field       | Offset | Length | Description |
   |-------------|--------|--------|-------------|
   | Command     | 0      | 4      | 'DMRD' |
   | Sequence    | 4      | 1      | Packet sequence number (0-255) |
   | RF Source   | 5      | 3      | Source radio ID (24-bit) |
   | Destination | 8      | 3      | Destination talkgroup/ID (24-bit) |
   | Radio ID    | 11     | 4      | Repeater ID (32-bit) |
   | _bits       | 15     | 1      | Control bits (see below) |
   | Stream ID   | 16     | 4      | Unique stream identifier |
   | Payload     | 20     | 33     | DMR voice/data payload |

   **_bits Field (byte 15):**
   - Bit 7: Timeslot (0=Slot 1, 1=Slot 2)
   - Bit 6: Call Type (0=Private, 1=Group)
   - Bits 4-5: Frame Type
     - `00` - Voice frame
     - `01` - Voice Sync (header/terminator)
     - `10` - Data Sync (header/terminator)
     - `11` - Unused
   - Bits 0-3: Reserved

   **DMR Stream Terminator Detection:**
   
   DMR transmissions end with an explicit terminator frame:
   - Frame Type = Voice Sync (0x01) or Data Sync (0x02)
   - Payload contains specific sync pattern indicating terminator vs header
   - When detected, stream ends immediately (~60ms after PTT release)
   - Enables fast slot turnaround for new transmissions
   
   **Sync Patterns** (in payload bytes 20-25):
   - Voice Header: `0x755FD7DF75F7`
   - Voice Terminator: `0xD5DD7DF75D55` ✅ **IMPLEMENTED**
   - Data Header: `0xDFF57D75DF5D`
   - Data Terminator: `0x7DFFD5F55D5F` ✅ **IMPLEMENTED**
   
   HBlink4 detects both voice and data terminators and ends streams immediately. Falls back to 2.0s timeout when terminator not received (packet loss).

## Connection Flow

1. **Initial Connection**
   ```
   Repeater                    Server
      |                          |
      |---------- RPTL -------->| (Login Request)
      |                          |
      |<-------- MSTCL ---------|
      |                          |
      |---------- RPTK -------->| (Authentication)
      |                          |
      |---------- RPTC -------->| (Configuration)
      |                          |
      |<-------- RPTA ---------|| (Accept)
   ```

2. **Keepalive Flow**
   ```
   Repeater                   Server
      |                         |
      |-------- RPTPING ------->|
      |<------- MSTPONG --------|
      |                         |
   ```

## Timing and Reliability

- Repeaters must send MSTP messages regularly to maintain the connection
- If a repeater misses multiple pings, the connection is considered dead
- Server responds to each MSTP with RPTACK + radio_id
- Connection timeouts should be handled gracefully with RPTCL messages

## Error Handling

- Invalid packets or state transitions are responded to with MSTNAK
- Authentication failures result in connection termination
- Missing keepalive responses should trigger reconnection attempts
- Configuration errors should be logged and connection reset

## Security Considerations

- All repeaters must authenticate before sending data
- Radio IDs must be validated
- Configuration data should be validated before acceptance
- Connection states must be strictly enforced
- Implement rate limiting for login attempts

## Implementation Notes

- All messages are UDP packets
- Radio IDs are 4 bytes long
- String commands (like 'RPTL') are ASCII encoded
- Binary data (like DMR voice) is raw bytes
- Implement appropriate timeouts for all states
- Log all protocol messages when debugging
