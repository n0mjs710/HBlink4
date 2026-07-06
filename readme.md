# HBlink4

HBlink4 is a DMR Server implementation using the HomeBrew protocol, developed by Cort Buffington, N0MJS. HBlink4 operates as an endpoint network server with granular per-repeater control; transit call-routing between DMR networks is limited.

There has been some chatter coming my way about what is "official" with respect to HBlink. I can't stop others from claiming they are the "official" source, or using account names that make them look officical. All I can say is that I'm the author, and I'm the copyright holder. What is the status?
 - **HBlink (DEPRECIATED):** Original Python2 based system, interacted with DMRlink for IPSC/HBP translations. Worked on a "system" level and supported internetworking via a "conference bridge" paradigm. Highly configurable for a time before DMRgateway.
 - **HBlink3 (ACTIVE DEVELOPMENT):** Rewrite in Python3, mostly the same code base updated. Dropped direct support for IPSC/HBP translations.
 - **HBlink4 (ACTIVE DEVLOPMENT):** Ground up re-write for modern times. Repeater, not system based. Supports full dynamic TGID subscription and TS/TGID translation based on "Options" configuration send by MMDVMHost or DMRGatway. Includes integrated dashboard application as part of the package.

For those who need Motorola IPSC connectivity, **see my companion project ipsc2hbp to convert from Motorola's proprietary network protocol to MMDVM's**.

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
- Built on Python's `asyncio` for single-loop UDP I/O with no external framework dependency
- **Tightly integrated web dashboard** - Real-time monitoring with modern look and feel (see [Dashboard Documentation](dashboard/README.md))
- **Stream tracking with immediate DMR terminator detection (~60ms)**
- **Real-time duration counter with 1-second updates**
- **Two-tier stream end detection (immediate terminator + timeout fallback)**
- **Per-repeater DMRD translation** - slot/TGID remap and outbound rf_src override declared via RPTO (see [DMRD Translation](docs/dmrd_translation.md))
- **OpenBridge (OBP) trunks** - stream-multiplexed, HMAC-authenticated server-to-server trunking to/from a core (e.g. HBlink3); many talkgroups over one socket (see [OpenBridge Trunks](docs/openbridge.md))
- **Unit (private) call routing** - User cache with broadcast fallback, subscriber-pair hang time, cross-slot support, and optional forwarding over outbound server links (implicit reverse-path tree when peers are HBlink4)
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
- **[Call Routing](docs/routing.md)** - Inbound/outbound filtering, contention, and assumed slot state
- **[DMRD Translation](docs/dmrd_translation.md)** - Per-repeater slot/TGID remap and rf_src override (RPTO extended syntax)
- **[OpenBridge Trunks](docs/openbridge.md)** - Stream-multiplexed server-to-server trunking (OBP): config, routing, dashboard, and the HBlink3-core/HBlink4-edge topology
- **[Stream Tracking](docs/stream_tracking.md)** - How DMR transmission streams are managed
- **[Stream Tracking Diagrams](docs/stream_tracking_diagrams.md)** - Visual walkthrough of stream lifecycle and contention
- **[Hang Time](docs/hang_time.md)** - Preventing conversation interruption
- **[Protocol Specification](docs/protocol.md)** - HomeBrew DMR protocol details
- **[Integration Guide](docs/integration.md)** - Using HBlink4 as a module
- **[Logging](docs/logging.md)** - Log management and rotation
- **[Roadmap / TODO](docs/TODO.md)** - Planned work (performance monitoring, multi-hop outbound, config UI)
- **[OpenBridge Analysis](docs/OPENBRIDGE_ANALYSIS.md)** - Background feasibility analysis (OpenBridge is now implemented — see [OpenBridge Trunks](docs/openbridge.md))
- **[Release Notes v4.8.0](docs/RELEASE_NOTES_v4.8.0.md)** - Current release — unit (private) call routing, DMR data-call classification, RPTO empty-slot fix
- **[Release Notes v4.7.0](docs/RELEASE_NOTES_v4.7.0.md)** - asyncio, DMRA, outbound connections, DMRD translation



## No Support Is Provided

This is not commercial software. It is provided free of charge. The author(s)
received no compensation for creating and maintaining it. Countless hours over
many years have gone into the this. If you have problems, the author will try
to help if possible, please have no expectations for support. There is no online
group, such as DVSwitch or groups.io that is an "official" outlet for information.
The only definitive source of information is me. Beware of others claiming to
be authoritative. User-based mutual support is great, and I'm all for it. But
please understand, this is what they are, and I have not sanctioned anyone to be
the "home" of my software packages.

### GitHub "Issues"

Do not use GitHub issues for support. Genuine bugs are accepted as issues. Before 
opening one, make sure that it is a true problem with the software and not merely
a misconfiguration, or contention around a feature that was not supported. Isssues
should never be used to ask for or recommend features. Issues that do not include
complete details, relevent tracebacks, error messages, configuration snippets, 
operatrional conditions surrounding the event, etc. will be closed without action.

## License

Copyright (C) 2016-2025 Cortney T. Buffington, N0MJS n0mjs@me.com
This project is licensed under the GNU GPLv3 License - see the LICENSE file for details.

## Acknowledgments

- Original HBlink3 by Cort Buffington, N0MJS
- The MMDVM and DMR community
