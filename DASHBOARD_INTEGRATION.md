# HBlink4 Dashboard Integration

## Overview

HBlink4 includes a tightly-integrated real-time web dashboard for monitoring DMR network activity. The dashboard receives events from HBlink4 and displays them in a modern, responsive interface with WebSocket-based live updates.

## Architecture

**Separate Processes:**
- **HBlink4 Server**: DMR protocol handler (Twisted/UDP)
- **Dashboard Server**: Web application (FastAPI/Uvicorn)
- **Communication**: Unix sockets (local) or TCP (remote) with automatic reconnection

**Event Flow:**
```
HBlink4 → EventEmitter → Transport (Unix/TCP) → Dashboard Server → WebSocket → Browser
```

## Transport Options

### Unix Socket (Recommended for Local)
- **Performance**: ~0.5-1μs per event
- **Security**: Filesystem-based permissions
- **Use case**: Dashboard on same server as HBlink4

### TCP (Required for Remote)
- **Performance**: ~5-15μs per event
- **Features**: IPv4/IPv6 dual-stack support
- **Use case**: Dashboard on different server
- **Security**: Use firewall rules to restrict access

See [Configuration Guide](docs/configuration.md#dashboard-configuration) for detailed setup.

## Features

- **Real-time Updates**: WebSocket-based live updates with no page refreshes
- **Repeater Monitoring**: Connected repeaters with health status
- **Active Streams**: Live transmission monitoring with duration counters
- **Last Heard Tracking**: Recent users with callsign lookup
- **Event Log**: Stream starts and ends
- **Statistics**: Aggregate metrics and activity tracking

See [Dashboard README](dashboard/README.md) for complete feature documentation.

## Installation

```bash
# Install HBlink4 dependencies
pip install -r requirements.txt

# Install dashboard dependencies
pip install -r requirements-dashboard.txt
```

## Configuration

Both HBlink4 and the dashboard must use the **same transport configuration**.

### HBlink4 Config (`config/config.json`)

```json
{
    "dashboard": {
        "enabled": true,
        "transport": "unix",
        "unix_socket": "/tmp/hblink4.sock"
    }
}
```

### Dashboard Config (`dashboard/config.json`)

```json
{
    "event_receiver": {
        "transport": "unix",
        "unix_socket": "/tmp/hblink4.sock"
    }
}
```

For remote dashboards, use `"transport": "tcp"` with appropriate `host_ipv4`, `host_ipv6`, and `port` settings in both configs.

See [Configuration Guide](docs/configuration.md#dashboard-configuration) for all options.

## Usage

### Start All Services (Recommended)
```bash
./run_all.sh
```

### Start Services Separately
```bash
# Terminal 1: Start HBlink4
python3 run.py

# Terminal 2: Start Dashboard
python3 run_dashboard.py
```

### Access Dashboard
Open your browser to: http://localhost:8080

## Event Types

| Event | Trigger | Data |
|-------|---------|------|
| repeater_connected | Registration complete | radio_id, callsign, address, talkgroups |
| repeater_keepalive | Periodic ping | radio_id, missed_pings |
| repeater_disconnected | Connection lost | radio_id, reason |
| stream_start | First packet | repeater_id, slot, src_id, dst_id |
| stream_update | Every 60 packets | duration, packet_count |
| stream_end | Terminator/timeout | duration, packets, end_reason |
| hang_time_expired | Hang time ends | repeater_id, slot |

## Performance

- **CPU Overhead**: < 0.001% (negligible)
- **Event Latency**: 0.5-15μs depending on transport
- **Update Frequency**: 1 second (60 packets)
- **Process Isolation**: Dashboard crash doesn't affect DMR operations

## Documentation

- **[Dashboard README](dashboard/README.md)** - Complete dashboard guide
- **[Configuration Guide](docs/configuration.md)** - HBlink4 and dashboard config
- **[Dashboard Transport](docs/dashboard_transport.md)** - Transport selection guide

## Support

For issues or questions:
1. Check documentation in `docs/` directory
2. Review dashboard logs for errors
3. Verify both configs use same transport settings
4. Check firewall rules if using TCP transport
