# HBlink4 Web Dashboard

Real-time monitoring dashboard for HBlink4 DMR server with modern look and feel.

## Features

- **Real-time Updates**: WebSocket-based live updates every second (no page refreshes required)
- **Repeater Monitoring**: See all connected repeaters with their configurations and connection health
- **Active Streams**: Monitor ongoing DMR transmissions in real-time with duration counters
- **Last Heard Tracking**: View the 10 most recent users with talker alias display
  - 10-minute user cache with configurable timeout
  - View full cache (up to 50 users) in modal dialog
  - Shows radio ID, callsign/alias, repeater, slot, talkgroup, and time
- **On-Air Event Log**: Track stream starts and ends for user-focused activity monitoring
- **Connection Monitoring**: Visual warnings for repeaters with missed keepalives
- **Statistics**: View total streams, packets, and activity metrics
- **Clean Design**: Dark theme with responsive layout that works on desktop and mobile
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

### Start All Services Together (Recommended)
```bash
./run_all.sh
```

This script starts both HBlink4 and the dashboard in the background.

### Access the Dashboard
Open your browser and navigate to:
- Local: http://localhost:8080
- Network: http://YOUR_SERVER_IP:8080

The dashboard will automatically connect via WebSocket and start displaying real-time data.

## Dashboard Components

### Statistics Cards
Top row displays key metrics:
- Connected repeaters count
- Active streams count
- Total streams processed
- Total packets received

### Last Heard Table
Shows the 10 most recent users:
- Radio ID
- Callsign/Alias (from talker alias, or "-" if not available)
- Repeater ID
- Slot (1 or 2)
- Talkgroup
- Time (e.g., "2 minutes ago")

Click "View Full Cache" to see up to 50 users with cache statistics.

### Active Streams
Real-time view of ongoing transmissions:
- Stream ID
- Radio ID
- Repeater ID
- Slot and Talkgroup
- Duration counter (updates every second)
- Packet count

### Connected Repeaters
List of all connected repeaters:
- Repeater ID and callsign
- IP address
- Connected duration
- Last keepalive time
- Configuration (mode, slots, colorcode)
- Warning indicator if keepalives are being missed

### Recent Events
User-focused activity log showing:
- Stream starts (user keyed up)
- Stream ends (user unkeyed)
- Excludes system events like keepalives and cache updates


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
