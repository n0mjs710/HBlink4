# Documentation Audit - October 5, 2025

## Summary

Comprehensive documentation audit completed to ensure all documentation accurately reflects the current code implementation. All configuration examples, API references, and usage instructions have been updated to match the actual working system.

## Changes Made

### 1. Configuration Structure Updates

**Updated Files:**
- `docs/configuration.md`
- `docs/dashboard_transport.md`
- `docs/TRANSPORT_IMPLEMENTATION_SUMMARY.md`
- `docs/IMPLEMENTATION_SUMMARY.md`
- `docs/integration.md`
- `dashboard/README.md`

**Changes:**
- ✅ Updated all config examples from `bind_ip`/`bind_port` to `bind_ipv4`/`bind_ipv6` and `port_ipv4`/`port_ipv6`
- ✅ Changed dashboard config from `host` to `host_ipv4`/`host_ipv6`
- ✅ Updated `ipv6` boolean flag to `disable_ipv6` throughout
- ✅ Corrected dashboard config location from `global.dashboard` to top-level `dashboard` section
- ✅ Added `user_cache` configuration documentation (mandatory feature)
- ✅ Removed references to removed config options (stats_interval, report_stats, path, ping_time)

### 2. Dual-Stack IPv6 Documentation

**Updated Files:**
- `docs/configuration.md`
- `docs/integration.md`
- `dashboard/README.md`

**Changes:**
- ✅ Documented dual-stack native operation (simultaneous IPv4 and IPv6)
- ✅ Explained `disable_ipv6` flag for networks with broken IPv6
- ✅ Added examples for IPv4-only, IPv6-only, and dual-stack configurations
- ✅ Clarified that empty string is NOT used to disable protocols (use disable_ipv6 flag instead)
- ✅ Updated integration examples to show proper dual-stack listener setup

### 3. Dashboard Configuration Clarity

**Updated Files:**
- `docs/configuration.md`
- `docs/dashboard_transport.md`
- `dashboard/README.md`
- `DASHBOARD_INTEGRATION.md`

**Changes:**
- ✅ Clarified that dashboard is TOP-LEVEL config, not nested under global
- ✅ Documented transport selection (Unix socket vs TCP) with clear use cases
- ✅ Explained which config fields are used for each transport type
- ✅ Added warning that both HBlink4 and dashboard configs must match
- ✅ Removed confusing references to fields not used by selected transport
- ✅ Added examples showing which fields to set for Unix vs TCP transport

### 4. Stream Management Accuracy

**Updated Files:**
- `docs/configuration.md`
- `docs/hang_time.md`

**Changes:**
- ✅ Clarified `stream_timeout` is FALLBACK only (primary detection is terminator frame at ~60ms)
- ✅ Emphasized that `stream_hang_time` is for slot reservation, not stream detection
- ✅ Updated recommended values with reasoning (2.0s timeout, 10-20s hang time)
- ✅ Removed references to "stream_timeout" as primary detection method

### 5. Symmetric Routing Documentation

**Updated Files:**
- `docs/configuration.md`

**Changes:**
- ✅ Clarified that empty talkgroup list `[]` means "accept/forward ALL" (symmetric)
- ✅ Documented that same list controls both inbound and outbound routing
- ✅ Added examples showing symmetric behavior

### 6. Removed Duplication

**Updated Files:**
- `DASHBOARD_INTEGRATION.md`
- `readme.md`

**Changes:**
- ✅ Streamlined DASHBOARD_INTEGRATION.md (removed development/testing notes)
- ✅ Removed duplicate config examples (now reference authoritative docs)
- ✅ Consolidated architecture info, removed outdated rollback procedures
- ✅ Updated main readme.md to reference comprehensive docs instead of duplicating

### 7. Accuracy Fixes

**Updated Files:**
- All documentation files

**Changes:**
- ✅ Fixed config file references from `hblink.json` to actual `config.json`
- ✅ Updated port numbers to match defaults (62031 not 54000)
- ✅ Corrected socket paths in examples
- ✅ Fixed pattern matching priority description
- ✅ Removed references to non-existent config fields

## Documentation Structure

### Primary Configuration Reference
- **`docs/configuration.md`** - THE authoritative source for all configuration options
  - Complete field definitions
  - All valid values explained
  - Examples for common scenarios
  - Clear warnings about common mistakes

### Specialized Documentation
- **`dashboard/README.md`** - Dashboard features and usage
- **`docs/dashboard_transport.md`** - Transport selection guide (Unix vs TCP)
- **`docs/hang_time.md`** - Hang time feature explanation
- **`docs/stream_tracking.md`** - Stream management internals
- **`docs/integration.md`** - Using HBlink4 as a module
- **`docs/protocol.md`** - DMR protocol details

### Summary Documents
- **`readme.md`** - Quick start and overview
- **`DASHBOARD_INTEGRATION.md`** - Dashboard integration summary
- **`docs/IMPLEMENTATION_SUMMARY.md`** - Features and architecture
- **`docs/TRANSPORT_IMPLEMENTATION_SUMMARY.md`** - Transport implementation details

## Verification Completed

### First Pass
- ✅ Audited all .md files
- ✅ Updated configuration structure references
- ✅ Fixed IPv6 setting names
- ✅ Corrected dashboard config location
- ✅ Updated integration examples

### Second Pass
- ✅ Searched for remaining `bind_ip` references (found none)
- ✅ Searched for old `host` field references (fixed all)
- ✅ Verified no `global.dashboard` structure references remain
- ✅ Confirmed all transport configurations use correct field names
- ✅ Validated consistency across all files

## Key Documentation Principles Applied

1. **Single Source of Truth**: `docs/configuration.md` is THE authoritative reference
2. **No Duplication**: Other docs reference configuration.md instead of repeating
3. **Code Follows Reality**: Documentation matches actual working implementation
4. **Clear Examples**: Each transport/deployment scenario has complete example
5. **Warnings Where Needed**: Common mistakes are explicitly called out

## Files Not Changed (Already Accurate)

- `docs/logging.md` - No config changes needed
- `docs/protocol.md` - Protocol spec unchanged
- `docs/TODO.md` - Future work unchanged
- `docs/routing.md` - Routing logic unchanged
- `docs/stream_tracking_diagrams.md` - Diagrams still accurate
- `scripts/README.md` - Script docs still accurate

## Configuration Field Reference (Post-Audit)

### Global Section
- `max_missed` - Max missed pings (default: 3)
- `timeout_duration` - Seconds between pings (default: 30)
- `disable_ipv6` - Disable IPv6 globally (default: false)
- `bind_ipv4` - IPv4 bind address (default: "0.0.0.0")
- `bind_ipv6` - IPv6 bind address (default: "::")
- `port_ipv4` - IPv4 port (default: 62031)
- `port_ipv6` - IPv6 port (default: 62031)
- `stream_timeout` - Fallback timeout (default: 2.0)
- `stream_hang_time` - Slot reservation (default: 10.0)
- `user_cache.timeout` - Cache expiry (default: 600, min: 60)

### Dashboard Section (Top-Level)
- `enabled` - Enable events (default: true)
- `disable_ipv6` - Disable IPv6 for dashboard (default: false)
- `transport` - "unix" or "tcp"
- `host_ipv4` - TCP IPv4 address
- `host_ipv6` - TCP IPv6 address
- `port` - TCP port (default: 8765)
- `unix_socket` - Unix socket path (default: "/tmp/hblink4.sock")
- `buffer_size` - Buffer size (default: 65536)

### Repeater Config Pattern
- `passphrase` - Authentication key (required)
- `slot1_talkgroups` - TS1 talkgroups ([] = all)
- `slot2_talkgroups` - TS2 talkgroups ([] = all)

## Removed References

The following outdated/removed config fields were purged from all documentation:
- ❌ `bind_ip` (replaced with bind_ipv4/bind_ipv6)
- ❌ `bind_port` (replaced with port_ipv4/port_ipv6)
- ❌ `use_ipv6` (replaced with disable_ipv6)
- ❌ `ipv6` boolean (replaced with disable_ipv6)
- ❌ `stats_interval` (removed feature)
- ❌ `report_stats` (removed feature)
- ❌ `path` (removed feature)
- ❌ `ping_time` (consolidated into timeout_duration)
- ❌ `host` (replaced with host_ipv4/host_ipv6)
- ❌ `enabled` in RepeaterConfig (removed field)
- ❌ `timeout` in RepeaterConfig (moved to global)
- ❌ `description` in RepeaterConfig (removed field)
- ❌ `talkgroups` in RepeaterConfig (replaced with slot1/slot2_talkgroups)

## Validation

Documentation now accurately reflects:
- ✅ Actual config file structure in `config/config.json`
- ✅ Actual config file structure in `dashboard/config.json`
- ✅ Working code in `hblink4/hblink.py`
- ✅ Working code in `dashboard/server.py`
- ✅ Working code in `hblink4/events.py`
- ✅ All 39 tests passing

## Completion Statement

**All documentation has been audited and updated to match the actual working implementation.**

No inconsistencies remain between documentation and code. All configuration examples use correct field names and values. Users can now confidently follow documentation to configure HBlink4 and the dashboard for any deployment scenario (local Unix socket, local TCP, or remote TCP with IPv4/IPv6).
