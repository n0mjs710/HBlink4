# HBlink4 TODO List

## Overview
This document tracks planned features and enhancements for HBlink4. Items are prioritized by importance and feasibility.

## High Priority

### 1. Code Refactoring - Reduce Repetition (High) 🔴
**Status**: Not started  
**Difficulty**: Medium  
**Dependencies**: None  
**Description**: Identify and consolidate repetitive code patterns across all files using helper functions to create single sources of truth.

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

**Estimated Effort**: 2-3 days

---

### 2. Dashboard Enhancements (Medium) 🟡
**Status**: In progress  
**Difficulty**: Medium  
**Dependencies**: Current dashboard  
**Description**: Improve real-time monitoring dashboard.

**Completed**:
- ✅ Real-time duration counter for active streams
- ✅ Automatic midnight reset for daily statistics
- ✅ Clickable connection status badges
- ✅ Event-driven updates (UDP events → WebSocket broadcasts)

**Practical Next Steps**:
- **Layout reorganization**: Move "Last Heard" table below repeaters table (better information hierarchy)
- **Mobile responsiveness**: Make dashboard usable on tablets (768px) and phones (375px)
  - Responsive table layouts
  - Collapsible sections
  - Compact mode for small screens

**Estimated Effort**: 1-2 days

---

## Medium Priority

### 2. Performance Monitoring (Easy) 🟢
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

**Estimated Effort**: 2-3 days

---

## Low Priority

### 3. Advanced Logging (Easy) 🟢
**Status**: Partial  
**Difficulty**: Low  
**Dependencies**: Current logging  
**Description**: Enhanced logging capabilities.

**Features**:
- Structured logging (JSON format)
- Log shipping (syslog, Elasticsearch)
- Per-repeater log levels
- Sensitive data filtering (privacy mode)
- Audit trail for configuration changes

**Implementation Notes**:
- Python `logging` handlers
- Configuration for log destinations

**Estimated Effort**: 3-5 days

---

### 4. Web-Based Configuration UI (Medium) 🟡
**Status**: Not started  
**Difficulty**: Medium  
**Dependencies**: Dashboard  
**Description**: GUI for editing configuration instead of JSON files.

**Features**:
- Repeater management (add/remove/edit)
- Access control rules editor
- Bridge configuration builder
- Configuration validation
- Live reload without restart

**Implementation Notes**:
- Extend FastAPI dashboard
- React/Vue frontend?
- Configuration backup/restore

**Estimated Effort**: 2-3 weeks

---

### 5. Protocol Extensions (Research) 🔵
**Status**: Research phase  
**Difficulty**: Unknown  
**Dependencies**: Community input  
**Description**: Explore enhancements to Homebrew protocol.

**Ideas**:
- Extended status reporting
- QoS fields
- Compression for high-traffic scenarios
- Authentication improvements

**Implementation Notes**:
- Requires community consensus
- Backward compatibility critical
- Document carefully

**Estimated Effort**: Unknown

---

## Completed Features ✅

### ✅ Stream Forwarding/Bridging
- Configuration-based call routing with slot-specific talkgroup lists
- Inbound filtering (which calls to accept from repeaters)
- Outbound filtering (which repeaters receive forwarded calls)
- Assumed slot state tracking for target repeaters
- Contention detection (prevent slot conflicts)
- Hang time respect on forwarding targets
- Forwarding statistics tracking
- Full documentation in `docs/routing.md`

### ✅ Stream Tracking
- Per-slot, per-repeater stream management
- Stream ID-based contention detection
- Packet counting and timing

### ✅ Hang Time
- Post-transmission slot reservation
- Configurable per-repeater
- Prevents conversation interruption

### ✅ Terminator Detection
- Immediate detection via packet header flags (~60ms)
- Frame type and dtype_vseq checking
- 3x faster than timeout-based detection

### ✅ Real-Time Duration Counter
- Live counting during active transmissions
- 1-second refresh rate
- Seamless transition to final duration on end

- ✅ Automatic midnight reset for daily statistics
- ✅ Clickable connection status badges

### ✅ Access Control Framework
- Radio ID blacklist/whitelist
- Pattern-based matching
- Per-repeater configuration

### ✅ Comprehensive Documentation
- Configuration guide
- Protocol specification
- **Stream forwarding/routing guide**
- Stream tracking diagrams
- Feature implementation guides
- Logging documentation

### ✅ Dashboard with Real-Time Updates
- WebSocket-based live data
- Stream status visualization
- Daily statistics tracking
- Automatic midnight reset

### ✅ Test Coverage
- 43+ unit tests across all features
- All tests passing
- Coverage for edge cases

---

## Notes on Prioritization

**High Priority** items are essential for production DMR network operation.

**Medium Priority** items add significant value but aren't critical for basic operation.

**Low Priority** items are "nice to have" or niche use cases.

**Research** items need more investigation before committing to implementation.

---

## Contributing

When working on TODO items:
1. Create a feature branch: `git checkout -b feature/item-name`
2. Update this TODO list with status changes
3. Add documentation in `docs/` for new features
4. Write comprehensive tests
5. Submit PR to `main` branch (after consultation for major features)

---

**Last Updated**: October 3, 2025
