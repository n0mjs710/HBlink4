# HBlink4 TODO List

## Overview
This document tracks planned features and enhancements for HBlink4. Items are prioritized by importance and feasibility.

## Ongoing Priority (Continuous)

### Code Refactoring - Reduce Repetition 🔄
**Status**: Ongoing at every milestone  
**Difficulty**: Medium  
**Dependencies**: None  
**Description**: Continuous process to identify and consolidate repetitive code patterns using helper functions to create single sources of truth. This is revisited at major milestones throughout the project.

**Goals**:
- Scan all Python files for duplicate/similar code patterns
- Extract common patterns into reusable helper functions
- Improve maintainability and reduce bugs from inconsistent implementations
- Build on recent success with `_end_stream()` helper consolidation

**Areas to Review**:
- Packet parsing and validation logic
- Event emission patterns
- Logging patterns
- Configuration validation
- Error handling patterns
- Data structure transformations

**Recent Success Example**:
- Consolidated 4 stream ending code paths into unified `_end_stream()` helper
- Resulted in: single source of truth, consistent behavior, easier maintenance

---

## Medium Priority

### 1. Performance Monitoring 🟢
**Status**: Not started  
**Difficulty**: Low  
**Dependencies**: None  
**Description**: Track and expose performance metrics.

**Critical Metrics**:
- **Latency**: Time from packet receipt to forwarding (most important)
- **Jitter**: Variance in latency (critical for voice quality)

**Additional Metrics**:
- Memory usage
- CPU usage
- Network bandwidth

**Implementation Notes**:
- Timestamp packets on receipt and forward
- Calculate rolling average latency and jitter per repeater
- Use Python `psutil` library for system metrics
- Expose via dashboard or Prometheus endpoint
- Minimal overhead

---

### 2. Multi-Hop Outbound Forwarding with Stream-ID Loop Detection 🟢
**Status**: Not started
**Difficulty**: Medium
**Dependencies**: None (would touch `_handle_outbound_dmr_data`, `_calculate_unit_call_targets`, and add a short-lived seen-stream cache)
**Description**: Today, traffic arriving on an outbound link is forwarded to local repeaters only — never to another outbound — for both group and unit calls. This is safe for star/tree topologies but prevents traffic transiting through a middle peer in chain or mesh topologies (A ↔ B ↔ C). Lifting that restriction requires loop prevention.

**The idea**: use the 4-byte DMR stream_id as a natural loop-detection token. Every transmission carries a unique random stream_id that's constant for the life of the stream (typically 2–3 s). When forwarding a packet across an outbound, record the stream_id in a short-TTL "seen" set. On receipt of a packet, if its stream_id is already in the set from a different ingress interface, drop it — it's a loop (or a duplicate from a peer that received the same broadcast via two paths).

**Why it fits**:
- Stream_id is already on the wire, so there's no protocol extension needed.
- 32-bit random space + short call duration means collisions are effectively zero within the window that matters.
- Structurally similar to BGP's AS_PATH loop check or IP's identification field — use a packet's own identifier rather than a separate protocol.

**Sketch**:
- Add a per-server `seen_streams: Dict[bytes, (ingress_identifier, expiry_time)]` with a 5–10 s TTL (long enough to cover a normal call + hang time, short enough to prevent unbounded growth).
- On receipt of a DMRD packet from any peer (local repeater or outbound), check the seen set:
  - If present with a different ingress → drop (loop)
  - If present with the same ingress → normal continuing-stream path
  - If absent → add to seen set, process normally
- When enabled, both `_handle_outbound_dmr_data` (group path) and `_handle_outbound_unit_call` would iterate outbounds as targets the same way they iterate local repeaters today.
- Gate behind a global config flag (default off) so existing deployments are unaffected.

**Edge cases to think through during design**:
- Stream_id reuse by a peer that restarted — unlikely given 32-bit random but should be bounded by TTL
- Legitimate duplicate paths (e.g., fan-in at a destination peer) vs actual loops
- Interaction with hang time and assumed-stream tracking — the existing stream_id comparison logic should still work

---

## Low Priority

### 3. Web-Based Configuration UI 🟡
**Status**: Not started  
**Difficulty**: Medium  
**Dependencies**: Dashboard  
**Description**: GUI for editing configuration instead of JSON files.

**Features**:
- Repeater management (add/remove/edit)
- Access control rules editor
- Configuration validation
- Live reload without restart

**Implementation Notes**:
- Extend FastAPI dashboard
- React/Vue frontend?
- Configuration backup/restore

---

**Last Updated**: April 21, 2026
