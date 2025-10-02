# HBlink4 Web Dashboard

Real-time monitoring dashboard for HBlink4 DMR server.

## Features

- **Real-time Updates**: WebSocket-based live updates every second
- **Repeater Monitoring**: See all connected repeaters with their configurations
- **Active Streams**: Monitor ongoing DMR transmissions in real-time
- **Event Log**: Track repeater connections, stream lifecycle, and hang time events
- **Statistics**: View total streams, packets, and activity metrics
- **Configurable Branding**: Customize server name and dashboard title

## Configuration

The dashboard can be customized via `config.json`:

```json
{
    "server_name": "My HBlink4 Server",
    "server_description": "Amateur Radio DMR Network",
    "dashboard_title": "HBlink4 Dashboard",
    "refresh_interval": 1000,
    "max_events": 50
}
```

**Configuration Options:**
- `server_name`: Displayed below the dashboard title (e.g., "KD0ABC Repeater Network")
- `server_description`: Reserved for future use
- `dashboard_title`: Main page title and browser tab text
- `refresh_interval`: WebSocket update interval in milliseconds (default: 1000)
- `max_events`: Maximum events to retain in event log (default: 50)

The config file is created automatically with defaults on first run. Edit `config.json` and refresh your browser to see changes.

## Installation

Install dashboard dependencies:
```bash
pip install -r requirements-dashboard.txt
```

Dependencies:
- `fastapi>=0.104.0` - Modern async web framework
- `uvicorn[standard]>=0.24.0` - ASGI server with WebSocket support
- `websockets>=12.0` - WebSocket protocol support

## Usage

### Start Dashboard Only
```bash
python3 run_dashboard.py [host] [port]

# Examples:
python3 run_dashboard.py                    # Default: 0.0.0.0:8080
python3 run_dashboard.py 127.0.0.1 8080    # Localhost only
python3 run_dashboard.py 0.0.0.0 3000      # Custom port
```

### Access the Dashboard
Open your browser and navigate to:
- Local: http://localhost:8080
- Network: http://YOUR_SERVER_IP:8080

The dashboard will automatically connect via WebSocket and start displaying real-time data.


## API Endpoints

The dashboard provides REST API endpoints:

### GET /api/config
Returns dashboard configuration

### GET /api/repeaters
Returns list of all connected repeaters

### GET /api/streams
Returns list of active and recently ended streams

### GET /api/events?limit=50
Returns recent events (default limit: 100)

### GET /api/stats
Returns aggregate statistics

### WebSocket /ws
Real-time updates via WebSocket

## Troubleshooting

**Dashboard shows "Disconnected"**
- Check if HBlink4 is running
- Verify WebSocket connection in browser console

**No events appearing**
- Verify HBlink4 is running and repeaters are connected
- Check browser console for errors

**WebSocket connection fails**
- Verify uvicorn is running with WebSocket support
- Try a different browser

## License

Same as HBlink4: GNU GPLv3
