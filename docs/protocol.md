# HomeBrew DMR Protocol Specification

This document describes the HomeBrew DMR protocol used for communication between DMR repeaters and servers. The protocol is based on UDP packets and implements a connection-oriented approach with authentication and keepalive mechanisms.

## Protocol Overview

The HomeBrew DMR protocol uses a series of commands exchanged between repeaters and servers to establish and maintain connections, authenticate devices, and transfer DMR data.

## Important DMR Specifications

- All Radio IDs are 32-bit (4-byte) fields
- This matches the DMR over-the-air protocol where IDs map to IPv4 addresses
- Radio IDs in all packets must be exactly 4 bytes, including any necessary leading zeros

## Connection States

A server will track a repeater in one of the following states:
- `no` - Not connected/initial state
- `rptl-received` - Login request received
- `waiting-config` - Authentication completed, waiting for configuration
- `yes` - Fully connected and operational

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
   - State Change: `waiting-config` → `yes`

5. **RPTCL (Repeater Close)**
   - Direction: Repeater → Server
   - Format: `RPTCL` + `radio_id[4 bytes]`
   - Purpose: Graceful connection termination

### Connection Maintenance

1. **RPTP (Repeater Ping)**
   - Direction: Repeater → Server
   - Format: `RPTP` + `radio_id[4 bytes]`
   - Purpose: Keepalive message
   - Timing: Sent periodically to maintain connection
   - Notes: 
     - Increments ping counter
     - Updates last_ping timestamp
     - Resets missed_pings counter

2. **MSTPONG (Server Pong)**
   - Direction: Server → Repeater
   - Format: `MSTPONG`
   - Purpose: Response to RPTP
   - Notes: Confirms server is still alive and connection is valid

3. **MSTP (Server Ping)**
   - Direction: Server → Repeater
   - Format: `MSTP`
   - Purpose: Server-initiated keepalive check

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

- Repeaters must send RPTP messages regularly to maintain the connection
- If a repeater misses multiple pings, the connection is considered dead
- Servers should respond to each RPTP with MSTPONG
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
