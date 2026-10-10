# HBlink4 Web Dashboard

Real-time monitoring dashboard for HBlink4 DMR server with modern look and feel.

BuenoDMR's separate, read-only administrative area is documented in
[BuenoDMR administration](../docs/buenodmr-admin.md).

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

The dashboard **listens**; HBlink4 **connects out to it** and sends the events.

Two configuration files:
- **HBlink4 config** (`config/config.json`, `dashboard` section) - the dashboard address HBlink4 connects to
- **Dashboard config** (`dashboard/config.json`, `event_receiver` section) - the addresses the dashboard binds and listens on, plus dashboard behavior

Both configs must use the same transport, port, and socket path (Unix socket for local, TCP for remote). The address fields are not mirrors of each other: one is a connect target, the other a bind address.

For complete configuration details, see the [Configuration Guide](../docs/configuration.md#dashboard-configuration).

The config file is created automatically with defaults on first run. Edit `dashboard/config.json` and restart the dashboard to apply changes.

### Announcement Banner

Display an optional announcement banner at the top of the dashboard to communicate important messages to users:

```json
{
  "announcement": {
    "enabled": true,
    "text": "Welcome to the network! Please review the talkgroup list before transmitting."
  }
}
```

**Configuration Options:**
- `enabled`: Set to `true` to show the banner, `false` to hide it
- `text`: The message to display in the banner

The banner appears as an eye-catching orange gradient banner at the top of the page, perfect for:
- Welcome messages
- Network announcements
- Maintenance notices
- Operational reminders
- Important policy updates

The banner only displays when both `enabled` is `true` and `text` contains content.

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

The dashboard is started automatically with `./run_all.sh`, or separately with `python3 run_dashboard.py` (or `python3 dashboard/server.py`) from the repository root. It binds the address and port from the `web` section of `dashboard/config.json` (default `0.0.0.0:8080`); passing `[bind] [port]` on the command line overrides that for one-off runs. Access at http://localhost:8080 (or your server IP for remote access).

## Dashboard Components

### Statistics Cards
Top row displays key metrics:
- **Connections**: Total connections (inbound repeaters/hotspots/network + connected outbound)
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
Connections are automatically categorized by device type:

- 📶 **Repeaters** - Full duplex repeaters and club sites (generic MMDVM, MMDVM_Unknown)
- 📱 **Hotspots** - Personal hotspots (MMDVM_HS boards, Pi-Star, WPSD, DMO/simplex)
- 🔗 **Network Inbound** - Servers connecting to us (HBlink, FreeDMR)
- ❓ **Other** - Unrecognized connection types

Each category shows:
- Repeater ID and callsign
- IP address
- Connected duration
- Last keepalive time
- Configuration (mode, slots, colorcode)
- Warning indicator if keepalives are being missed

Click any connection card to view detailed information including:
- Software ID and Package ID (for troubleshooting detection)
- Connection Type (repeater, hotspot, network, unknown)
- Location, frequencies, and access control settings

Detection is based on the `package_id` field from MMDVM/Pi-Star/WPSD configuration, with fallback to `software_id`. Customize detection patterns in `config/config.json` under `connection_type_detection`. See the [Configuration Guide](../docs/configuration.md#connection-type-detection) for details.

### Recent Events
User-focused activity log showing:
- Stream starts (user keyed up)
- Stream ends (user unkeyed)
- Excludes system events like keepalives and cache updates


## API Endpoints

The BuenoDMR `Admin` link opens `/admin`. Its authenticated operator
management, apply/rollback procedure, and backup policy are documented in
[BuenoDMR administration](../docs/buenodmr-admin.md).

The public browser page uses an intentionally small, allowlisted API:

- `GET /api/public/snapshot` returns public network status, aggregate counts,
  connected hotspot callsigns, recent TG100 voice activity, and active operator
  callsigns. The response is not a raw projection of HBlink4 state.
- `WebSocket /ws/public` sends the same sanitized snapshot at connection time
  and when the dashboard state changes.

The former raw `/api/*` routes and `/ws` telemetry socket are unavailable to
visitors. Administrative operator management remains under `/admin` and its
server-side authorization checks. See [BuenoDMR administration](../docs/buenodmr-admin.md)
and [radio access security](../docs/radio-access-security.md) for the details.

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
