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

### Callsign Lookups

The dashboard downloads the radio ID database from radioid.net once a day and uses it to show callsigns in Last Heard. **By default it only loads the United States and Canada**, so a radio from anywhere else shows `-` instead of a callsign. If that's you, change the `countries` list in the `user_database` section of `dashboard/config.json`:

```json
{
  "user_database": {
    "filter": {
      "countries": ["New Zealand"],
      "callsign_regex": null,
      "radio_id_ranges": null
    }
  }
}
```

**Filter options:**
- `countries`: A list of country names, spelled exactly as radioid.net spells them (e.g., `["United Kingdom", "Ireland"]`), or the string `"all"`, without brackets, for every country. `"all"` loads over 300,000 entries, roughly twice the default, so list only the countries you need if you can
- `callsign_regex`: Only load callsigns matching this Python regular expression, or `null` for no restriction
- `radio_id_ranges`: Only load IDs in these ranges, e.g. `[[5300000, 5309999]]`, or `null` for no restriction

**When a change takes effect:** restart the dashboard to read the new config. The new filter is then applied at the next daily download (03:17 by default, set by `refresh.time_of_day`) once radioid.net has published a new file, which it normally does daily. Until then, the dashboard keeps using the data it already has.

**To apply it now**, delete the saved database and restart. The dashboard downloads a fresh copy within a few seconds of starting:

```bash
rm dashboard/data/user_db.json dashboard/data/user_db.meta.json
sudo systemctl restart hblink4-dash
```

Delete both files: the second one records when the last download happened, and keeping it can prevent the new download. If the filter matches nothing, such as a misspelled country, the dashboard logs a warning and keeps the data it had. Callsigns are looked up when a call starts, so a call already in Last Heard keeps showing `-` until that radio transmits again.

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

**Last Heard shows `-` instead of a callsign**
- The radio's country is probably not in your filter: see [Callsign Lookups](#callsign-lookups)
- The ID may not be registered at radioid.net
- Check the dashboard log for `user_db` warnings: `journalctl -u hblink4-dash | grep user_db`

**WebSocket connection fails**
- Verify uvicorn is running with WebSocket support
- Try a different browser

## License

Same as HBlink4: GNU GPLv3
