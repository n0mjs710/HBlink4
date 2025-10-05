# HBlink4 TODO List

## Overview
This document tracks planned features and enhancements for HBlink4. Items are prioritized by importance and feasibility.

## High Priority

### 1. Code Refactoring - Reduce Repetition (High) 🔴
**Status**: Ongoing 
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

---

## Low Priority

### 3. Web-Based Configuration UI (Medium) 🟡
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

**Last Updated**: October 3, 2025
