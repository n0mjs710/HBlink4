# HBlink4 TODO List

## Overview
This document tracks planned features and enhancements for HBlink4. Items are prioritized by importance and feasibility.

## Ongoing Priority (Continuous)

### Code Refactoring - Reduce Repetition �
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

## High Priority

### 1. Unit/Private Call Handling 🔴
**Status**: In progress (detection/logging implemented)  
**Difficulty**: High  
**Dependencies**: User cache (✅ exists), Options parsing (needs extension)  
**Description**: Implement full subscriber-to-subscriber (unit-to-unit) private call routing with intelligent target discovery and efficient bandwidth usage.

**Phase 1: Configuration & Control** ✅ (detection only)
- [x] Early detection of unit calls (call_type_bit == 1)
- [x] Log unit calls clearly with source/dest/stream_id
- [x] Reject unit calls (return False) until full implementation ready
- [ ] Extend options parsing to handle `UNIT=true|false` in repeater options string
  - Parse alongside existing `TS1=...;TS2=...` format
  - Store in RepeaterState as `unit_calls_enabled` boolean
- [ ] Add `default_unit_calls` field to pattern configuration
  - Used when repeater doesn't send UNIT option
  - Per-pattern default (allows different networks different defaults)
- [ ] Update configuration documentation with unit call options

**Phase 2: User Cache Enhancement**
- [ ] Add `slot` field tracking to user cache entries (CRITICAL - see note below)
  - Currently tracks: radio_id, repeater_id, callsign, talkgroup, last_heard
  - **Need to add**: slot (1 or 2) where user was last heard
  - **Reason**: Unit calls can only connect users on the same slot (no slot translation)
- [ ] Update user cache on every transmission to include slot
- [ ] Add lookup function: `get_repeater_and_slot_for_user(radio_id) -> (repeater_id, slot)`

**Phase 3: Unit Call Routing - Target Known (One-to-One)**
- [ ] When unit call detected from unit-enabled repeater:
  1. Extract source subscriber ID (rf_src) and dest subscriber ID (dst_id)
  2. Look up target in user cache: `get_repeater_and_slot_for_user(dst_id)`
  3. **Check slot compatibility**: Source slot must match target's last-heard slot
     - If slots don't match: Log warning, reject call (cannot translate slots)
  4. If target found and slots match:
     - Build route cache with single target repeater
     - Create StreamState with unit call metadata (source_sub, dest_sub, source_repeater, dest_repeater)
     - Forward packets only to target repeater
     - Track as one-to-one unit call
  5. Reserve slots on ONLY source and destination repeaters (others remain free)

**Phase 4: Unit Call Routing - Target Unknown (One-to-Many Broadcast)**
- [ ] When target not in cache (or cache expired):
  1. Build route cache with ALL unit-enabled repeaters (except source)
  2. Filter by slot: Only include repeaters where target slot is available
  3. Create StreamState marked as "broadcast unit call"
  4. Forward packets to all candidate repeaters
  5. Reserve slot on source + all broadcast target repeaters
- [ ] Implement route cache pruning on first response:
  1. Detect response: New stream from any target repeater with swapped IDs
     - Original: src=A, dst=B from repeater_1
     - Response: src=B, dst=A from repeater_2
  2. When response detected:
     - Prune forward route cache (A→B) to only include repeater_2
     - Build reverse route cache (B→A) to only include repeater_1
     - Release slots on all other target repeaters
     - Update user cache with target's location (B is on repeater_2, slot X)
     - Log: "Unit call established: A@repeater_1 ↔ B@repeater_2"

**Phase 5: Unit Call State Management**
- [ ] Modify StreamState to include unit call fields:
  - `source_subscriber_id`: Originating radio ID
  - `dest_subscriber_id`: Target radio ID (for private calls)
  - `is_unit_call`: Boolean flag
  - `is_broadcast_unit_call`: Boolean flag (target unknown)
- [ ] Stream direction tracking:
  - Each transmission (PTT press) is a new stream (same as group calls)
  - Forward direction: Lock source_sub on source_repeater → dest_sub on dest_repeater
  - Reverse direction: Lock dest_sub on dest_repeater → source_sub on source_repeater
  - Both directions must respect the established repeater pair
- [ ] Contention handling for unit calls:
  - Check slot availability same as group calls
  - **Additional check**: Enforce source/dest subscriber ID pairs
    - Block: Different subscriber trying to use unit call slot
    - Allow: Same subscriber pair continuing conversation
  - If user switches repeaters during call: Block until hang time expires
    - User must wait for hang time, then start new unit call
    - Reason: Prevents routing table corruption, maintains call integrity

**Phase 6: Hang Time & Slot Reservation**
- [ ] Apply same hang time rules as group calls:
  - Same user continuing: ALLOW (any subscriber)
  - Fast switching: ALLOW (same source subscriber, different target)
  - Multi-party: N/A for unit calls (always point-to-point)
  - Hijacking: DENY (different subscriber pair)
- [ ] Slot reservation rules:
  - One-to-one: Only source_repeater[slot] and dest_repeater[slot] reserved
  - Broadcast: Source_repeater[slot] + all_candidate_repeaters[slot] reserved
  - After pruning: Release slots on non-participating repeaters immediately
  - Other slot on same repeater: FREE for other traffic

**Phase 7: Logging & Monitoring**
- [ ] Enhanced logging for unit calls:
  - Stream start: "UNIT CALL: A@rep1:TS1 → B@rep2:TS1 (one-to-one)"
  - Stream start: "UNIT CALL: A@rep1:TS1 → B@unknown (broadcast to 5 repeaters)"
  - Pruning: "UNIT CALL: Pruned broadcast, B found on rep2, now one-to-one"
  - Slot mismatch: "UNIT CALL: Rejected, A on TS1 but B last heard on TS2"
  - Direction switch: "UNIT CALL: Reverse direction, B@rep2:TS1 → A@rep1:TS1"
- [ ] Dashboard events for unit calls:
  - `unit_call_start`: Initial call attempt
  - `unit_call_established`: Target found/responded
  - `unit_call_end`: Call terminated
  - Include source/dest IDs, repeaters, and call duration

**Phase 8: Performance & Efficiency** ⚠️
- [ ] Monitor and measure:
  - Route cache lookup performance (should remain O(1))
  - Route cache pruning overhead (should be minimal, happens once per call)
  - Memory overhead of broadcast unit calls
  - CPU impact of subscriber ID pair validation
- [ ] Efficiency constraints:
  - **If broadcast overhead too high**: Consider limiting max broadcast repeaters
  - **If pruning too slow**: Consider async pruning or delayed pruning
  - **If validation expensive**: Consider caching valid pairs during call
  - **Fallback plan**: Make unit calls opt-in only for smaller networks if performance degraded

**Testing Strategy**:
1. Test one-to-one (target in cache, same slot)
2. Test slot mismatch rejection
3. Test broadcast (target not in cache)
4. Test pruning when target responds
5. Test hang time protection
6. Test user switching repeaters mid-call (should block)
7. Test both call directions (A→B, then B→A)
8. Test multiple concurrent unit calls on different slots
9. Performance test: 100 concurrent unit calls

**Implementation Notes**:
- Reuse existing group call code where possible (hang time, terminator detection, etc.)
- User cache MUST include slot field (cannot route without it)
- No slot translation (unit calls only work if both users on same TS)
- Repeater switching mid-call blocked until hang time expires
- Each transmission is a new stream (same as group calls)
- Route cache is prunable (one-to-many → one-to-one optimization)

---

## Medium Priority

### 2. Performance Monitoring 🟢
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

**Last Updated**: October 11, 2025
