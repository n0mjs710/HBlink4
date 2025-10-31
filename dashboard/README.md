# HBlink4 Web Dashboard

Real-time monitoring dashboard for HBlink4 DMR server with modern look and feel.

## Features

- **Real-time Updates**: WebSocket-based live updates every second (no page refreshes required)
- **Repeater Monitoring**: See all connected repeaters with their configurations and connection health
- **Active Streams**: Monitor ongoing DMR transmissions in real-time with duration counters
- **Last Heard Tracking**: View the 10 most recent users with alias display
  - 10-minute user cache with configurable timeout
  - View full cache (up to 50 users) in modal dialog
  - Shows radio ID, callsign/alias, repeater, slot, talkgroup, and time
- **On-Air Event Log**: Track stream starts and ends for user-focused activity monitoring
- **Connection Monitoring**: Visual warnings for repeaters with missed keepalives
- **Statistics**: View total streams, packets, and activity metrics
- **Clean Design**: Dark theme with responsive layout that works on desktop and mobile
- **Configurable Branding**: Customize server name and dashboard title
- **Network Info Button**: Optional button linking to network information page

## Configuration

The dashboard uses two configuration files:
- **HBlink4 config** (`config/config.json`) - Controls event sending
- **Dashboard config** (`dashboard/config.json`) - Controls dashboard behavior and event receiving

Both configs must use the same transport settings (Unix socket for local, TCP for remote).

For complete configuration details, see the [Configuration Guide](../docs/configuration.md#dashboard-configuration).
"port": 8765
```

The config file is created automatically with defaults on first run. Edit `dashboard/config.json` and restart the dashboard to apply changes.

### Network Info Button

Add an optional "Network Info" button to the dashboard header that links users to your network's information page:

```json
{
  "network_info": {
    "enabled": true,
    "button_text": "Network Info",
    "url": "https://your-network-site.com/info"
  }
}
```

**Configuration Options:**
- `enabled`: Set to `true` to show the button, `false` to hide it
- `button_text`: Custom text for the button (e.g., "Network Info", "TG List", "Help")
- `url`: Target URL that opens in a new tab when clicked

The button appears as the first item in the header status area with a light blue color to distinguish it from system status indicators. Perfect for linking to talkgroup lists, network rules, connection information, or help pages.

## Usage

The dashboard is started automatically with `./run_all.sh` or can be started separately with `python3 run_dashboard.py`. Access at http://localhost:8080 (or your server IP for remote access).

## Dashboard Components

### Statistics Cards
Top row displays key metrics:
- **Connected Repeaters**: Number of repeaters currently connected
- **Active Streams**: Number of streams currently in progress (RX and TX)
- **Total Calls Today**: Number of calls received from repeaters today (RX only)
- **Retransmitted Calls Today**: Number of calls forwarded to other repeaters today (TX)
- **Total Traffic Today**: Duration of received traffic today (RX only)

### Last Heard Table
Shows the 10 most recent users:
- Radio ID
- Callsign/Alias (or "-" if not available)
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
