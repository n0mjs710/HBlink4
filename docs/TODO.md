# HBlink4 TODO List

## Overview
This document tracks planned features and enhancements for HBlink4. Items are prioritized by importance and feasibility.

## High Priority

### 1. Stream Forwarding/Bridging (Complex) 🔴
**Status**: Not started  
**Difficulty**: High  
**Dependencies**: None  
**Description**: Forward DMR streams between repeaters based on configuration rules.

**Requirements**:
- Bridge configuration format
- Target repeater selection logic
- Packet forwarding engine
- Stream tracking per destination
- Prevent forwarding loops

**Implementation Notes**:
- Core routing logic needed
- May need to modify destination IDs
- Must preserve or rebuild LC if modifying frames
- Test with multiple repeaters

**Estimated Effort**: 2-3 weeks

---

### 2. Enhanced Access Control (Medium) 🟡
**Status**: Not started  
**Difficulty**: Medium  
**Dependencies**: Current access control framework  
**Description**: Expand access control with more granular permissions.

**Features**:
- Per-talkgroup permissions (not just global allow/deny)
- Time-based restrictions (e.g., only allow 9-5)
- Emergency call prioritization
- Private call policies
- Blacklist/whitelist per repeater

**Implementation Notes**:
- Extend existing access control module
- New configuration schema
- May need separate permission levels

**Estimated Effort**: 1 week

---

### 3. Dashboard Enhancements (Medium) 🟡
**Status**: In progress  
**Difficulty**: Medium  
**Dependencies**: Current dashboard  
**Description**: Improve real-time monitoring dashboard.

**Completed**:
- ✅ Real-time duration counter for active streams
- ✅ Automatic midnight reset for daily statistics
- ✅ Clickable connection status badges

**Remaining**:
- Stream history view (last N transmissions)
- Repeater statistics graphs (traffic over time)
- Audio player integration (if storing recordings)
- Talker alias display in dashboard
- Map view of repeater locations
- Alert notifications (WebSocket push)

**Estimated Effort**: Ongoing (1-2 days per feature)

---

## Medium Priority

### 4. LC Extraction from Sync Frames (Complex) 🟠
**Status**: Not started  
**Difficulty**: High  
**Dependencies**: None  
**Description**: Extract Link Control from voice header/terminator sync frames.

**Current State**:
- LC already available from DMRD packet header (call type, IDs)
- Embedded LC extraction from voice frames working (when header missed)
- This would provide redundant/backup LC source

**Use Cases**:
- **Stream forwarding with LC modification**: When bridging streams and changing source/destination IDs, we need to rebuild LC data in sync frames
- **Verification**: Compare LC from multiple sources for consistency
- **Recovery**: Extract LC even if DMRD header is corrupted

**Implementation Notes**:
- LC is embedded in ETSI sync patterns at bytes 14-19
- May need FEC (Forward Error Correction) decoding
- HBlink3 uses dmr_utils3 library for this
- Would enable rebuilding entire DMR frames from scratch

**Why Lower Priority**:
- Current methods work fine for read-only use cases
- Only critical when implementing stream forwarding with LC modification
- More complex than other features

**Estimated Effort**: 1-2 weeks (research + implementation)

---

### 5. Stream Recording (Medium) 🟡
**Status**: Not started  
**Difficulty**: Medium  
**Dependencies**: Storage backend  
**Description**: Record DMR audio streams to disk for playback/archival.

**Features**:
- Capture raw AMBE frames
- Transcode to MP3/WAV (requires AMBE codec)
- Metadata storage (who, when, talkgroup)
- Automatic cleanup/rotation
- Privacy controls (exclude private calls?)

**Implementation Notes**:
- AMBE codec may require licensing
- Large storage requirements
- Configuration for recording policies

**Estimated Effort**: 2-3 weeks

---

### 6. Performance Monitoring (Easy) 🟢
**Status**: Not started  
**Difficulty**: Low  
**Dependencies**: None  
**Description**: Track and expose performance metrics.

**Metrics**:
- Packets per second
- Active streams count
- Memory usage
- CPU usage
- Network bandwidth
- Latency measurements

**Implementation Notes**:
- Use Python `psutil` library
- Expose via dashboard or Prometheus endpoint
- Minimal overhead

**Estimated Effort**: 2-3 days

---

## Low Priority

### 7. Advanced Logging (Easy) 🟢
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

### 8. Multi-Server Clustering (Complex) 🔴
**Status**: Not started  
**Difficulty**: Very High  
**Dependencies**: Stream forwarding, distributed state  
**Description**: Run multiple HBlink4 instances with shared state.

**Requirements**:
- Distributed stream tracking
- Load balancing repeater connections
- Failover/high availability
- State synchronization (Redis/etcd?)

**Implementation Notes**:
- Architectural redesign required
- Complex consensus protocols
- May not be needed for typical deployments

**Estimated Effort**: 4-6 weeks

---

### 9. Web-Based Configuration UI (Medium) 🟡
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

### 10. Protocol Extensions (Research) 🔵
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

### 11. Mobile App Integration (Medium) 🟡
**Status**: Not started  
**Difficulty**: Medium  
**Dependencies**: API endpoints  
**Description**: Mobile app for monitoring/control.

**Features**:
- Real-time stream monitoring
- Push notifications for events
- Quick enable/disable repeaters
- View statistics on mobile

**Implementation Notes**:
- REST API for mobile client
- WebSocket for real-time updates
- Separate project (iOS/Android)

**Estimated Effort**: 4-6 weeks

---

## Completed Features ✅

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

### ✅ DMR Link Control Extraction
- Call type, source, destination from DMRD header
- Embedded LC from voice frames (backup method)
- Automatic extraction and storage

### ✅ Talker Alias Extraction
- Multi-format support (7-bit, ISO-8859-1, UTF-8, UTF-16)
- Assembly across multiple frames
- Full FLCO handling

### ✅ Access Control Framework
- Radio ID blacklist/whitelist
- Pattern-based matching
- Per-repeater configuration

### ✅ Comprehensive Documentation
- Configuration guide
- Protocol specification
- Stream tracking diagrams
- Feature implementation guides

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

## LC Decoding Clarification

**Previous rationale** for LC extraction was to enable:
1. ~~Sync pattern detection~~ ✅ **SOLVED** (using packet header flags)
2. ~~Immediate terminator detection~~ ✅ **SOLVED** (60ms detection working)

**Current rationale** for LC extraction:
1. **Stream forwarding with ID modification**: When bridging streams between networks, we may need to rewrite source/destination IDs. This requires:
   - Extracting LC from incoming frames
   - Modifying the LC fields
   - Rebuilding LC with correct FEC encoding
   - Re-embedding LC in voice header/terminator sync frames
   
2. **Frame reconstruction**: Building DMR frames from scratch for routing/bridging

3. **Advanced features**: Emergency call handling, privacy management, etc.

**Bottom line**: LC extraction from sync frames is not urgent for current features, but will be needed when implementing stream forwarding/bridging with modified routing.

---

## Contributing

When working on TODO items:
1. Create a feature branch: `git checkout -b feature/item-name`
2. Update this TODO list with status changes
3. Add documentation in `docs/` for new features
4. Write comprehensive tests
5. Submit PR to `main` branch (after consultation for major features)

---

**Last Updated**: October 2, 2025
