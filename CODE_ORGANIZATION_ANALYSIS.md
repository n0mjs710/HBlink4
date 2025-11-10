# Code Organization Analysis - hblink.py

## Current Structure Issues

### 1. **Scattered Related Functions**
- Address validation methods spread out
- Stream handling methods not grouped
- Packet processing helpers interspersed with main logic
- Event emission methods separated from related functionality

### 2. **Helper Methods Mixed with Core Logic**
- `_parse_dmr_packet()` (line ~1169) far from `_handle_dmr_data()` (line ~2581)
- Address methods (`_normalize_addr`, `_addr_matches`) early in file but used throughout
- TG formatting helpers in middle of main protocol logic

### 3. **Poor Logical Grouping**
- No clear separation between:
  - Utility/Helper functions
  - Inbound repeater logic
  - Outbound connection logic
  - Stream management
  - Timeout/Maintenance tasks

## Proposed Reorganization

### **Phase 1: Group by Functional Area**

```python
class HBProtocol(asyncio.DatagramProtocol):
    def __init__(self, *args, **kwargs):
        # Constructor logic
    
    # ========== CORE PROTOCOL METHODS ==========
    def connection_made(self, transport):
    def connection_lost(self, exc):
    def datagram_received(self, data: bytes, addr: PeerAddress):
    def cleanup(self) -> None:
    
    # ========== UTILITY & HELPER METHODS ==========
    @staticmethod
    def _normalize_addr(addr: PeerAddress) -> Tuple[str, int]:
    def _addr_matches(self, addr1: PeerAddress, addr2: PeerAddress) -> bool:
    def _addr_matches_repeater(self, repeater: RepeaterState, addr: PeerAddress) -> bool:
    def _rid_to_int(self, repeater_id: bytes) -> int:
    def _safe_decode_bytes(self, data: bytes) -> str:
    def _format_tg_display(self, tg_set: Optional[set]) -> str:
    def _format_tg_json(self, tg_set: Optional[set]) -> Optional[list]:
    def _prepare_repeater_event_data(self, repeater_id: bytes, repeater: RepeaterState) -> dict:
    
    # ========== PACKET PARSING & VALIDATION ==========
    def _parse_dmr_packet(self, data: bytes) -> Optional[Dict[str, Any]]:
    def _validate_repeater(self, repeater_id: bytes, addr: PeerAddress) -> Optional[RepeaterState]:
    def _is_slot_busy(self, repeater_id: bytes, slot: int, stream_id: bytes, ....) -> bool:
    
    # ========== INBOUND REPEATER MANAGEMENT ==========
    def _handle_repeater_login(self, repeater_id: bytes, addr: PeerAddress) -> None:
    def _handle_auth_response(self, repeater_id: bytes, auth_hash: bytes, addr: PeerAddress) -> None:
    def _handle_repeater_config(self, repeater_id: bytes, data: bytes, addr: PeerAddress) -> None:
    def _handle_repeater_disconnect(self, repeater_id: bytes, addr: PeerAddress) -> None:
    def _remove_repeater(self, repeater_id: bytes, reason: str = "disconnect") -> None:
    def _load_repeater_tg_config(self, repeater_id: bytes, repeater: RepeaterState) -> None:
    def _emit_repeater_details(self, repeater_id: bytes, repeater: RepeaterState) -> None:
    
    # ========== OUTBOUND CONNECTION MANAGEMENT ==========
    def _parse_options(self, options: str) -> Tuple[Optional[set], Optional[set]]:
    def start_outbound_connections(self) -> None:
    def _handle_outbound_packet(self, connection_name: str, data: bytes, addr: tuple):
    def _send_outbound_config(self, state: OutboundState, addr: tuple):
    def _send_outbound_options(self, state: OutboundState, addr: tuple):
    def _handle_outbound_dmr_data(self, data: bytes, outbound_state: OutboundState):
    
    # ========== STREAM MANAGEMENT ==========
    def _emit_stream_start(self, connection_type: str, connection_id: str, ...):
    def _emit_stream_end(self, connection_type: str, connection_id: str, ...):
    def _handle_stream_start(self, repeater: RepeaterState, rf_src: bytes, dst_id: bytes, ...):
    def _end_stream(self, stream: StreamState, repeater_id: bytes, slot: int, ...):
    
    # ========== DMR PACKET PROCESSING ==========
    def _handle_dmr_data(self, data: bytes, addr: PeerAddress) -> None:
    def _check_inbound_routing(self, repeater_id: bytes, slot: int, tgid: int) -> bool:
    def _check_outbound_routing(self, repeater_id: bytes, slot: int, tgid: int) -> bool:
    
    # ========== TIMEOUT & MAINTENANCE ==========
    def _check_timeout(self, connection_type: str, connection_id: str, ...):
    def _check_slot_timeout(self, repeater_id: bytes, repeater: RepeaterState, ...):
    def _check_outbound_slot_timeout(self, conn_name: str, outbound: OutboundState, ...):
    def _check_stream_timeouts(self):
    def _check_repeater_timeouts(self):
    def _cleanup_user_cache(self):
    
    # ========== NETWORK I/O ==========
    def _send_packet(self, data: bytes, addr: PeerAddress) -> None:
    def _send_ack(self, repeater_id: bytes, addr: PeerAddress) -> None:
    def _send_nak(self, repeater_id: bytes, addr: PeerAddress, reason: str = "General") -> None:
    def _send_initial_state(self):
```

### **Benefits of This Organization:**

1. **Logical Grouping**: Related functions are together
2. **Hot Path Locality**: Packet processing functions are grouped
3. **Maintenance Clarity**: Timeout/cleanup functions are sectioned
4. **Developer Efficiency**: Easy to find related functionality
5. **Potential Cache Benefits**: Related code loaded together

### **Implementation Strategy:**

1. **Start with safe moves** - functions that have no interdependencies
2. **Group utilities first** - helper methods that are widely used
3. **Move hot path functions together** - packet parsing near packet handling
4. **Test after each logical group** - ensure no breaking changes

### **Specific Reorganization Opportunities:**

#### **Immediate Wins (Safe to Move):**
- Group all `_format_*` methods together
- Move `_parse_dmr_packet()` near `_handle_dmr_data()`
- Cluster all address validation methods
- Group all stream emission methods

#### **Medium Risk (Dependency Analysis Needed):**
- Timeout checking methods
- Repeater state management
- Event emission coordination

#### **High Impact Areas:**
- Hot path: `datagram_received()` → packet parsing → DMR handling
- Stream lifecycle: start → processing → timeout → end
- Connection lifecycle: login → auth → config → active → disconnect