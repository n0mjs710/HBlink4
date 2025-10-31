# HBlink4

HBlink4 is a DMR Server implementation using the HomeBrew protocol, developed by Cort Buffington, N0MJS. HBlink4 operates as an endpoint network server with granular per-repeater control and does not implement transit call-routing between DMR networks.

## Architecture

HBlink4 focuses on being an efficient **endpoint network server** with the following design principles:

- **Per-repeater routing rules** using TS/TGID tuples for precise call handling
- **Individual repeater management** rather than server-level "system" groupings
- **Direct source connectivity** without multi-hop relay complexity
- **Granular per-repeater control and monitoring**
- **Tightly integrated web dashboard** - Real-time monitoring with WebSocket updates

## Features

- **Native dual-stack IPv4/IPv6 support** - Simultaneous listening on both protocols for maximum compatibility
- Modern Python implementation with type hints
- Improved error handling and logging
- JSON-based configuration
- Enhanced repeater management
- Built on Twisted framework for reliable async operation
- **Tightly integrated web dashboard** - Real-time monitoring with modern look and feel (see [Dashboard Documentation](dashboard/README.md))
- **Stream tracking with immediate DMR terminator detection (~60ms)**
- **Real-time duration counter with 1-second updates**
- **Two-tier stream end detection (immediate terminator + timeout fallback)**
- **User routing cache for efficient private call routing**
- Pattern-based repeater configuration and blacklisting
- Per-slot transmission management

## Installation

> **⚠️ IMPORTANT**: Clone and run HBlink4 as the same user account. The systemd service files are configured to run as the user who owns the installation directory. The dashboard writes files for persistence across restarts and needs write access as well.

1. Clone this repository:
```bash
git clone https://github.com/yourusername/HBlink4.git
cd HBlink4
```

2. Create a virtual environment and activate it:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install requirements:
```bash
pip install -r requirements.txt
```

## Configuration

Copy the sample configuration file and modify it for your needs:
```bash
cp config/config_sample.json config/config.json
```

See the [Configuration Guide](docs/configuration.md) for complete documentation of all settings.

## Running

### Production (systemd services)
For production deployments with automatic startup, see [SYSTEMD.md](SYSTEMD.md).

### Development
```bash
# Start all services together
./run_all.sh

# Or start services separately:
python3 run.py              # HBlink4 server
python3 run_dashboard.py    # Web dashboard (in another terminal)
```

Access the dashboard at http://localhost:8080. See [Dashboard Documentation](dashboard/README.md) for features and configuration.

## Documentation

Comprehensive documentation is available in the `docs/` directory:

- **[Configuration Guide](docs/configuration.md)** - Complete configuration reference with all settings explained
- **[Dashboard README](dashboard/README.md)** - Dashboard features and usage
- **[Systemd Service Installation](SYSTEMD.md)** - Production deployment with automatic startup
- **[Connecting Repeaters](docs/connecting_to_hblink4.md)** - How to connect repeaters to HBlink4
- **[Stream Tracking](docs/stream_tracking.md)** - How DMR transmission streams are managed
- **[Hang Time](docs/hang_time.md)** - Preventing conversation interruption
- **[Protocol Specification](docs/protocol.md)** - HomeBrew DMR protocol details
- **[Integration Guide](docs/integration.md)** - Using HBlink4 as a module
- **[Logging](docs/logging.md)** - Log management and rotation



## Support

No end-user support is provided. The development team for this software is exactly 1 person, with limited resources and no ability to duplicate environments for testing. Flagging genuine code issues is apprecicated, but please to not open issues because it doesn't work the way you'd like it to. There are no feature requests.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. Do not submit a Pull Request to the main branch for added features without consultation first. Added features may collide with other mainline features under development, and additions that are inconsistent with the goals of the project may not be accepted. If you want to add a feature, it's best to discuss it first. Use alternative branches named for the feature being added.

## License

Copyright (C) 2016-2025 Cortney T. Buffington, N0MJS n0mjs@me.com
This project is licensed under the GNU GPLv3 License - see the LICENSE file for details.

## Acknowledgments

- Original HBlink3 by Cort Buffington, N0MJS
- The MMDVM and DMR community
