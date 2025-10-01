# HBlink4

HBlink4 is the next evolution of the HBlink DMR Server implementation using the HomeBrew protocol, developed by Cort Buffington, N0MJS. This version represents a complete architectural redesign, moving from a server-centric to a repeater-centric model for more granular control and management of connected systems.

## Key Architectural Changes

- Elimination of peer mode - HBlink4 operates purely as a server
- Individual repeater management rather than server-level system management
- Direct repeater registration without binding to specific server instances
- Granular per-repeater control and monitoring

## Features

- Modern Python implementation with type hints
- Improved error handling and logging
- JSON-based configuration
- Enhanced repeater management
- Built on Twisted framework for reliable async operation
- Stream tracking with DMR terminator detection and hang time
- Two-tier stream end detection (terminator frame + timeout fallback)
- Pattern-based repeater configuration and blacklisting
- Per-slot transmission management

## Installation

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

Copy the example configuration file and modify it for your needs:
```bash
cp config/hblink.json config/my_hblink.json
```

Edit the configuration file to set up your access control, repeater options, and routing rules. For detailed configuration instructions, see the [Configuration Guide](docs/configuration.md).

## Running

To start HBlink4:
```bash
python3 hblink4/hblink.py config/my_hblink.json
```

The server will start and listen for repeater registrations on the configured bind_ip and bind_port.

## Documentation

Comprehensive documentation is available in the `docs/` directory:

- **[Configuration Guide](docs/configuration.md)** - Complete configuration reference
- **[Stream Tracking](docs/stream_tracking.md)** - How DMR transmission streams are managed
- **[Stream Tracking Diagrams](docs/stream_tracking_diagrams.md)** - Visual flow diagrams
- **[Hang Time](docs/hang_time.md)** - Preventing conversation interruption
- **[Protocol Specification](docs/protocol.md)** - HomeBrew DMR protocol details
- **[Integration Guide](docs/integration.md)** - Using HBlink4 as a module
- **[Logging](docs/logging.md)** - Log management and rotation

## Configuration 

The configuration file uses JSON format and supports extensive customization of server behavior, access control, and routing rules. For detailed configuration instructions and examples, see the [Configuration Guide](docs/configuration.md).

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. Do not submit a Pull Request to the main branch for added features without consultation first. Added features may collide with other mainline features under development. Use alternative branches named for the feature being added.

## License

This project is licensed under the GNU GPLv3 License - see the LICENSE file for details.

## Acknowledgments

- Original HBlink3 by Cort Buffington, N0MJS
- The MMDVM and DMR community
