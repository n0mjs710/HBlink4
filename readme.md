# HBlink4

HBlink4 is the next evolution of the HBlink DMR Server implementation using the HomeBrew protocol, developed by Cort Buffington, N0MJS. This version represents a complete architectural redesign reflecting a fundamental shift in how DMR networks operate.

## Architectural Philosophy: From Transit Router to Endpoint Server

When I developed HBlink3, DMR networks required transit call routing—servers had to relay traffic between networks, much like Internet autonomous systems. Repeaters could only connect to one server, so if you wanted access to multiple networks (KS-DMR, Brandmeister, DMR-MARC), your regional network had to act as a transit router, forwarding calls to and from other national or international networks.

The landscape changed dramatically with the development of **DMRGateway** by the MMDVM team. This software allows repeaters to split traffic by timeslot and talkgroup (TS/TGID tuples), connecting directly to multiple servers simultaneously. Repeaters can now reach different networks as primary sources, eliminating the need for transit routing.

**HBlink4 embraces this new paradigm.** Rather than implementing complex transit routing features, HBlink4 focuses on being an efficient **endpoint network server** with granular per-repeater control. This architectural shift enables:

- **Per-repeater routing rules** using TS/TGID tuples for precise call handling
- **Individual repeater management** rather than server-level "system" groupings
- **Direct source connectivity** without multi-hop relay complexity
- **Simplified architecture** focused on what modern networks actually need

HBlink4 will not implement transit call routing. The focus of HBlink has always been to enable smaller, regional neworks to operate with autonomy, and not be subject to rules, regulations, whims, desires and decrees pushed down by the "big network" operators. HBlink also no longer plays into the notion that a self-proclaimed authority should govern the issuance of endpoint IDs, TGIDs, etc. and as such will not implement subscriber ID aliasing from the central source. That's great for folks who want one big homogenous world-wide network -- but by and large, that assumes that everyone's purpose for using DMR is unfettered world-wide access to every other endpoint. I have go grudge against that, but I'm more interested in radio and repeaters. I am not interested in creating a global VOIP network where hotspots are the primary target -- largely reducing the radio amatuer's use of radio to be, effectively, the same capabilities as a bluetooth wireless handset.

## Key Architectural Changes

- Elimination of peer mode - HBlink4 operates purely as a server
- Individual repeater management rather than server-level system management
- Direct repeater registration without binding to specific server instances
- Granular per-repeater control and monitoring
- **Tightly integrated web dashboard with modern look and feel** - Real-time monitoring with WebSocket updates, no page refreshes required

## Features

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
- **DMR Link Control (LC) metadata extraction from packet headers and voice frames**
- **Embedded LC reassembly with 4-frame accumulation**
- **Talker alias extraction with multi-format support (7-bit, ISO-8859-1, UTF-8, UTF-16)**
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

### Start all services (recommended)
```bash
./run_all.sh
```

This starts both HBlink4 and the web dashboard. Access the dashboard at http://localhost:8080

### Start services separately
```bash
# Start HBlink4 server
python3 run.py

# In another terminal, start the dashboard
python3 run_dashboard.py
```

The server will listen for repeater registrations on the configured bind_ip and bind_port.

For detailed dashboard features and configuration, see the [Dashboard Documentation](dashboard/README.md).

## Documentation

Comprehensive documentation is available in the `docs/` directory:

- **[Configuration Guide](docs/configuration.md)** - Complete configuration reference
- **[Stream Tracking](docs/stream_tracking.md)** - How DMR transmission streams are managed
- **[Stream Tracking Diagrams](docs/stream_tracking_diagrams.md)** - Visual flow diagrams
- **[Hang Time](docs/hang_time.md)** - Preventing conversation interruption
- **[LC Extraction](docs/lc_extraction.md)** - DMR Link Control metadata extraction
- **[TODO List](docs/TODO.md)** - Planned features and enhancements (11 items)
- **[Implementation Summary](docs/IMPLEMENTATION_SUMMARY.md)** - Completed features overview
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
