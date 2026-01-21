# OpenBridge Protocol Analysis for HBlink4

## Executive Summary

OpenBridge is an alternative sub-protocol within the HomeBrew DMR specification designed specifically for **server-to-server communication**. Unlike the standard HomeBrew Protocol (HBP) which emulates a repeater-to-server relationship with full login/authentication/configuration handshake, OpenBridge is a **connectionless, HMAC-authenticated** protocol optimized for high-throughput inter-server traffic.

This document analyzes the feasibility, performance implications, and architectural considerations of integrating OpenBridge support into HBlink4.

---

## Protocol Comparison

### Standard HomeBrew Protocol (Current HBlink4 Implementation)

| Aspect | Description |
|--------|-------------|
| **Connection Model** | Connection-oriented (state machine) |
| **Authentication** | Multi-step: RPTL → MSTCL (salt) → RPTK (hash) → RPTACK → RPTC (config) |
| **Keepalive** | Required: RPTPING/MSTPONG every N seconds |
| **Packet Format** | DMRD (53 bytes) - no per-packet auth |
| **Identity** | Radio ID (4 bytes) - pretends to be a repeater |
| **Configuration** | Full metadata exchange (callsign, freq, location, etc.) |
| **Slot Handling** | Proper TDMA: TS1/TS2 tracked independently |
| **Use Case** | Server accepting repeaters OR server pretending to be repeater |

### OpenBridge Protocol (Proposed Addition)

| Aspect | Description |
|--------|-------------|
| **Connection Model** | Connectionless (no state machine) |
| **Authentication** | Per-packet HMAC-SHA1 (20 bytes appended to each packet) |
| **Keepalive** | None required |
| **Packet Format** | DMRD (53 bytes) + HMAC (20 bytes) = **73 bytes** |
| **Identity** | Network ID (4 bytes) - identifies the server/network |
| **Configuration** | None - no config exchange |
| **Slot Handling** | Single-slot by default (TS1), `BOTH_SLOTS` flag for dual-slot |
| **Use Case** | Server-to-server linking for high-volume traffic |

---

## HBlink3 OpenBridge Implementation Details

### Class Structure (from hblink3/hblink.py)

```python
class OPENBRIDGE(DatagramProtocol):
    def __init__(self, _name, _config, _report):
        self._laststrid = deque([], 20)  # Last 20 stream IDs for dedup
        # No login state machine - just config
    
    def send_system(self, _packet):
        # Append HMAC and send
        _packet = b''.join([_packet[:11], self._config['NETWORK_ID'], _packet[15:]])
        _packet = b''.join([_packet, hmac_new(self._config['PASSPHRASE'], _packet, sha1).digest()])
        self.transport.write(_packet, (self._config['TARGET_IP'], self._config['TARGET_PORT']))
    
    def datagramReceived(self, _packet, _sockaddr):
        # Only accept DMRD packets
        if _packet[:4] == DMRD:
            _data = _packet[:53]
            _hash = _packet[53:]
            _ckhs = hmac_new(self._config['PASSPHRASE'], _data, sha1).digest()
            
            # Validate HMAC and source address
            if compare_digest(_hash, _ckhs) and _sockaddr == self._config['TARGET_SOCK']:
                # Parse and process...
```

### Key Characteristics

1. **No Connection State**: No `connection_state` tracking - packets are validated independently
2. **Per-Packet HMAC**: Every packet carries SHA1-HMAC authentication (20 bytes overhead)
3. **Deduplication via Deque**: Uses `deque([], 20)` to track last 20 stream IDs for duplicate detection
4. **Network ID**: Replaces Radio ID in packet header (bytes 11-14) with a "Network ID"
5. **Fixed Target**: Single `TARGET_IP:TARGET_PORT` - no dynamic peer management
6. **No Keepalive**: Connection health inferred from traffic flow

### Stream Tracking Differences

| Aspect | HBP (HBlink4) | OpenBridge (HBlink3) |
|--------|---------------|---------------------|
| Key | `slot` (1 or 2) | `stream_id` |
| Structure | `repeater.slot1_stream` / `slot2_stream` | `self.STATUS[_stream_id]` |
| Lifecycle | Per-slot, cleared on terminator | Per-stream-id, popped on terminator |
| Contention | Slot-based (TDMA) | Stream-ID based |

---

## Performance Analysis

### Hot Path Impact (Call Forwarding)

The **critical hot path** in HBlink4 is `_forward_stream()` → packet processing → socket write. Let's analyze OpenBridge's impact:

#### Current HBP Flow (No Per-Packet Auth)
```
datagram_received → _handle_dmr_data → _forward_stream → sendto()
                                                          ↓
                                                    [53 bytes]
```
**Time**: ~50-100µs per packet (dominated by socket ops)

#### Proposed OpenBridge Flow (Per-Packet HMAC)
```
datagram_received → HMAC verify → _handle_dmr_data → _forward_stream → HMAC sign → sendto()
                        ↓                                                 ↓
                   [~15-20µs]                                        [~15-20µs]
                                                                          ↓
                                                                    [73 bytes]
```

**HMAC Overhead**:
- `hmac_new(passphrase, packet, sha1).digest()` ≈ **15-25µs** per call (Python)
- For **receiving** OpenBridge: +15-20µs verification
- For **sending** to OpenBridge: +15-20µs signing
- **Total hot path impact**: ~30-40µs additional latency per OpenBridge-routed packet

**Packet Size Overhead**:
- HBP: 53 bytes/packet
- OpenBridge: 73 bytes/packet (+38% bandwidth)
- At 50 packets/second voice stream: 1000 bytes/sec additional overhead

#### Optimization Opportunities

1. **Use `hashlib` C implementation**: Python's `hmac` module uses fast C code - already optimal
2. **Pre-compute passphrase padding**: HMAC key setup can be cached
3. **Separate threads for OpenBridge targets**: Offload HMAC to worker threads (complex)
4. **Hardware acceleration**: Modern CPUs have SHA instructions, but Python GIL limits benefit

### Dashboard Impact

Current dashboard receives events via EventEmitter (`stream_start`, `stream_end`, `stream_update`). OpenBridge adds no additional event types - streams are streams regardless of transport.

**Potential Concerns**:
1. **Higher event volume**: OpenBridge links typically carry more traffic (they exist specifically for high-volume inter-server links)
2. **Network ID vs Repeater ID display**: Dashboard shows "Repeater 315001" - would need to show "Network OpenBridge-1" or similar
3. **No metadata**: OpenBridge doesn't exchange callsign/location - dashboard shows less info

---

## Proposed Architecture

### Option A: Dual-Class Design (Recommended)

Similar to HBlink3, create a separate `OpenBridgeProtocol` class:

```python
class OpenBridgeProtocol(asyncio.DatagramProtocol):
    """
    UDP Protocol for OpenBridge server-to-server links.
    Connectionless, HMAC-authenticated per-packet.
    """
    def __init__(self, name: str, config: OpenBridgeConfig, event_emitter: EventEmitter):
        self.name = name
        self.config = config  # target_ip, target_port, network_id, passphrase
        self._events = event_emitter
        self._laststrid = deque(maxlen=20)  # Stream dedup
        self.STATUS: Dict[bytes, StreamState] = {}  # Keyed by stream_id, not slot
    
    def datagram_received(self, data: bytes, addr: tuple):
        # Validate HMAC, source address
        # Parse DMRD
        # Stream tracking by stream_id
        # Forward to main HBProtocol for local distribution
    
    def send_packet(self, packet: bytes):
        # Replace Network ID, append HMAC, send
```

**Integration with HBProtocol**:
```python
class HBProtocol:
    def __init__(self):
        self._openbridge_links: Dict[str, OpenBridgeProtocol] = {}
    
    def _forward_stream(self, ...):
        # ... existing repeater forwarding ...
        
        # Forward to OpenBridge links
        for name, obp in self._openbridge_links.items():
            if obp.should_receive(slot, dst_id):
                obp.send_packet(data)
```

**Pros**:
- Clean separation of concerns
- OpenBridge complexity isolated
- Easier to test independently
- Matches HBlink3 architecture (familiar to users)

**Cons**:
- Code duplication (stream tracking logic)
- Two protocol classes to maintain
- Cross-class communication needed

### Option B: Unified Protocol with Transport Abstraction

Extend existing `HBProtocol` to handle both transport types:

```python
@dataclass
class ServerLink:
    """Base class for server links"""
    name: str
    transport: asyncio.DatagramTransport
    authenticated: bool = True  # OpenBridge is always "authenticated"

@dataclass  
class OpenBridgeLink(ServerLink):
    """OpenBridge-specific state"""
    network_id: bytes
    passphrase: bytes
    target_addr: tuple
    slot_handling: str = "single"  # or "both"
    
    def sign_packet(self, data: bytes) -> bytes:
        # Replace Network ID, append HMAC
        
    def verify_packet(self, data: bytes) -> bool:
        # Verify HMAC, return stripped data

class HBProtocol:
    def __init__(self):
        self._outbounds: Dict[str, OutboundState] = {}  # HBP connections
        self._openbridge: Dict[str, OpenBridgeLink] = {}  # OBP connections
```

**Pros**:
- Shared stream tracking infrastructure
- Single forwarding loop
- Less code overall

**Cons**:
- Mixes concerns (auth vs no-auth)
- More complex conditionals in hot path
- Type system gets messy

---

## Configuration Design

### Proposed Config Format (JSON)

```json
{
  "openbridge_connections": [
    {
      "enabled": true,
      "name": "BM-USA",
      "target_ip": "127.0.0.1",
      "target_port": 62031,
      "network_id": 310001,
      "passphrase": "secret",
      "both_slots": false,
      "slot1_talkgroups": [3100, 3120, 310],
      "slot2_talkgroups": null,
      "acl": {
        "subscriber": "PERMIT:ALL",
        "talkgroup": "PERMIT:ALL"
      }
    }
  ]
}
```

### Compatibility with Existing Systems

OpenBridge is used by:
- **Brandmeister**: Primary inter-server protocol
- **DMR+**: Inter-server links
- **TGIF**: Network interconnection
- **Other HBlink servers**: Legacy bridging

---

## Pros and Cons Summary

### Pros of Adding OpenBridge

1. **Industry Standard**: Required for Brandmeister/DMR+ integration
2. **No Keepalive Overhead**: One less periodic task per connection
3. **No Config Negotiation**: Faster "connection" establishment
4. **Simpler State**: No connection state machine to manage
5. **Familiar to Users**: HBlink3 users expect this capability

### Cons of Adding OpenBridge

1. **Per-Packet HMAC Overhead**: ~30-40µs additional latency per packet
2. **38% Bandwidth Increase**: 73 vs 53 bytes per packet
3. **No Metadata Exchange**: Can't display callsign/location on dashboard
4. **Security Model Difference**: Shared secret vs per-repeater passwords
5. **Stream Tracking Complexity**: Need stream_id-based tracking (not slot-based)
6. **Code Complexity**: Another protocol variant to maintain
7. **Testing Matrix Expansion**: HBP→HBP, HBP→OBP, OBP→HBP, OBP→OBP

---

## Performance Impact Matrix

| Scenario | Latency Impact | CPU Impact | Memory Impact |
|----------|---------------|------------|---------------|
| Receive from OBP | +15-20µs | +1 HMAC verify | +1 StreamState dict |
| Forward to OBP | +15-20µs | +1 HMAC sign | Negligible |
| 10 OBP targets | +150-200µs | +10 HMAC ops | +10 entries in STATUS |
| Dashboard (OBP streams) | None | None | Same as HBP |

### Realistic Load Scenario

Assume: 100 concurrent voice streams, 50 packets/sec/stream, 5 OpenBridge targets

**Per Second**:
- Packets processed: 5,000
- HMAC operations (verify incoming): 5,000 × 15µs = 75ms
- HMAC operations (sign for each target): 5,000 × 5 × 15µs = 375ms
- **Total HMAC time**: 450ms/sec = **45% of one CPU core**

**Mitigation**: Real deployments rarely have 100 concurrent streams. 10-20 streams is more typical, which would be ~45-90ms/sec (5-10% of one core).

---

## Recommendations

### Short Term (v4.2.0)
1. **Do not implement** OpenBridge until outbound connections are stable and proven
2. Focus on optimizing existing HBP forwarding path
3. Gather user feedback on outbound connection feature

### Medium Term (v4.3.0 or v5.0.0)
1. Implement OpenBridge using **Option A (Dual-Class Design)**
2. Make it opt-in via config flag
3. Add `--no-openbridge` CLI flag for performance-critical deployments
4. Comprehensive benchmarking before/after

### Implementation Priority
1. Core `OpenBridgeProtocol` class
2. Config parsing and validation
3. Integration with `_forward_stream()` 
4. Dashboard display (show Network ID, note missing metadata)
5. Documentation and migration guide from HBlink3

---

## Decision Checkpoint

Before proceeding with implementation, answer:

1. **Is Brandmeister/DMR+ integration a user requirement?** If most users only connect local repeaters, OpenBridge adds complexity without benefit.

2. **Is the 45% CPU overhead acceptable?** For high-traffic servers, this may require hardware upgrades.

3. **Can dashboard UX accommodate missing metadata?** OpenBridge connections won't show callsign/location.

4. **Is the testing burden sustainable?** Four protocol combinations multiply test cases.

---

## Appendix: HBlink3 OpenBridge Config Format

For reference, HBlink3 uses `.cfg` format:

```ini
[OBP-1]
MODE = OPENBRIDGE
ENABLED = True
NETWORK_ID = 310001
IP = 0.0.0.0
PORT = 62031
PASSPHRASE = password
TARGET_IP = 127.0.0.1
TARGET_PORT = 62031
BOTH_SLOTS = False
USE_ACL = True
SUB_ACL = DENY:1
TGID_ACL = PERMIT:ALL
```
