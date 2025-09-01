# HomeBrew DMR Protocol Specification

This document describes the HomeBrew DMR protocol used for communication between DMR re      |<-------- MSTP -------->|
      |<------- RPTACK --------|
      |                          |rs and servers. The protocol is based on UDP packets and implements a connection-oriented approach with authentication and keepalive mechanisms.

## Protocol Overview

The HomeBrew DMR protocol uses a series of commands exchanged between repeaters and servers to establish and maintain connections, authenticate devices, and transfer DMR data.

## Important DMR Specifications

- All Radio IDs are 32-bit (4-byte) fields
- This matches the DMR over-the-air protocol where IDs map to IPv4 addresses
- Radio IDs in all packets must be exactly 4 bytes, including any necessary leading zeros

## Connection States

A server will track a repeater in one of the following states:
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
   | Color Code    | 36     | 2      | DMR color code |
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

1. **MSTP (Repeater Ping)**
   - Direction: Repeater → Server
   - Format: `MSTP` + `radio_id[4 bytes]`
   - Purpose: Repeater keepalive message
   - Notes: 
     - Sent periodically by repeater
     - Server responds with RPTACK + radio_id
     - Updates last_ping timestamp
     - Resets missed_pings counter

2. **RPTACK (Server Response)**
   - Direction: Server → Repeater
   - Format: `RPTACK` + `radio_id[4 bytes]`
   - Purpose: Acknowledge messages from repeater
   - Usage:
     - Server sends in response to MSTP
     - Confirms server received and accepted message

### Data Transfer

1. **DMRD (DMR Data)**
   - Direction: Bidirectional
   - Format: `DMRD` + `sequence[4 bytes]` + `radio_id[4 bytes]` + `data`
   - Purpose: Transfer DMR voice/data packets
   - Notes: Only processed when connection state is 'yes'

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
   Repeater                    Server
      |                          |
      |---------- RPTP -------->|
      |<------- MSTPONG --------|
      |                          |
      |<-------- MSTP ----------|
      |--------- RPTACK ------->|
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
