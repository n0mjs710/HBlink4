# HBlink4 Copilot Instructions

## Project Overview

HBlink4 is a DMR Server implementing the HomeBrew protocol for amateur radio repeater networks. It's a **repeater-centric** architecture focusing on per-repeater control rather than server-level groupings. The system consists of two main components:

- **Core server** (`hblink4/`): Asyncio-based UDP server handling DMR protocol, stream tracking, and access control
- **Web dashboard** (`dashboard/`): FastAPI-based real-time monitoring with WebSocket updates

## Architecture Patterns

### Component Structure
- `hblink4/hblink.py`: Main server with asyncio UDP protocol handlers
- `hblink4/events.py`: Event emission to dashboard (TCP/Unix socket transport abstraction)
- `hblink4/access_control.py`: Pattern-based repeater matching and blacklisting
- `hblink4/models.py`: Dataclasses for `RepeaterState`, `StreamState`, `OutboundConnectionConfig`
- `dashboard/server.py`: FastAPI app with WebSocket real-time updates

### Configuration Pattern
JSON-based configs in `config/` with pattern matching:
```json
{
  "repeater_configurations": {
    "patterns": [
      {
        "match": {"ids": [123], "callsigns": ["N0MJS*"], "id_ranges": [[315000, 315999]]},
        "config": {"enabled": true, "timeout": 30, "talkgroups": [3120]}
      }
    ]
  }
}
```

### Import Strategy
Modules use dual import paths (package-relative first, fallback to direct):
```python
try:
    from .constants import RPTA, RPTL
    from .utils import safe_decode_bytes
except ImportError:
    from constants import RPTA, RPTL
    from utils import safe_decode_bytes
```

## Development Workflows

### Running & Testing
```bash
# Development setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dashboard.txt

# Run server only
python run.py [config/config.json]

# Run dashboard only  
python run_dashboard.py [host] [port]

# Run both (production-like)
./run_all.sh

# Tests
python -m pytest tests/ -v
python -m pytest tests/test_access_control.py::TestRepeaterMatcher::test_specific_id_match -v
```

### Transport Configuration
Dashboard communication uses transport abstraction:
- **Unix socket** (default): `/tmp/hblink4.sock` - fastest for localhost
- **TCP**: IPv4/IPv6 dual-stack - for remote dashboard

Both `config/config.json` and `dashboard/config.json` must use matching transport settings.

### Stream Tracking Pattern
Real-time DMR stream monitoring with dual termination detection:
- **Immediate**: DMR terminator packet detection (~60ms)
- **Fallback**: Timeout-based cleanup (configurable `stream_timeout`)

Event emission happens every 10 superframes (1 second) for dashboard updates.

## Key Integration Points

### Access Control Flow
1. `RepeaterMatcher.find_config()` matches incoming repeater against patterns
2. Checks blacklist patterns first (raises `BlacklistError`)
3. Returns `RepeaterConfig` with per-repeater settings
4. Pattern types: `specific_id`, `id_range`, `callsign` (with wildcards)

### Event System
`EventEmitter` sends JSON events to dashboard:
- Stream events: `stream_start`, `stream_end`, `stream_update`
- Repeater events: `repeater_connected`, `repeater_disconnected`
- Uses non-blocking sockets with connection state tracking

### User Cache
`UserCache` manages private call routing with TTL expiration:
```python
user_cache.update_user(radio_id, repeater_id, slot)
target_repeater = user_cache.lookup_user(target_id)
```

## File Organization

- **Core logic**: `hblink4/` package with protocol handling
- **Configuration**: `config/config.json` (server), `dashboard/config.json` (web)
- **Scripts**: `run.py`, `run_dashboard.py`, `run_all.sh`
- **Deployment**: Systemd services expect user ownership, virtual environment
- **Data**: `logs/`, `dashboard/data/` for persistence
- **Tests**: `tests/` with pytest, focus on access control and stream tracking

When editing protocol handling, ensure proper terminator detection in `protocol.py`. For dashboard changes, maintain WebSocket message format compatibility. Access control changes should validate against test patterns in `tests/test_access_control.py`.