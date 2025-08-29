# HBlink4

HBlink4 is the next evolution of the HBlink DMR Master Server implementation using the HomeBrew protocol, developed by Cort Buffington, N0MJS. This version represents a complete architectural redesign, moving from a master-centric to a repeater-centric model for more granular control and management of connected systems.

## Key Architectural Changes

- Elimination of peer mode - HBlink4 operates purely as a master server
- Individual repeater management rather than master-level system management
- Direct repeater registration without binding to specific master instances
- Granular per-repeater control and monitoring

## Features

- Modern Python implementation with type hints
- Improved error handling and logging
- JSON-based configuration
- Enhanced repeater management
- Built on Twisted framework for reliable async operation

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

## Configuration File Structure

The configuration file (hblink.json) contains four main sections that reflect the repeater-centric architecture:

### Global Settings
```json
"global": {
    "path": "./",
    "ping_time": 5,
    "max_missed": 3,
    "use_ipv6": false,
    "bind_ip": "0.0.0.0",
    "bind_port": 62031,
    "log_level": "INFO",
    "log_file": "logs/hblink.log"
}
```

### Access Control
```json
"access_control": {
    "default_policy": "deny",
    "authentication": {
        "required": true,
        "default_passphrase": "your_default_passphrase",
        "rules": [
            {
                "type": "radio_id",
                "pattern": "1234567",
                "passphrase": "specific_pass_1",
                "description": "Exact match for single radio ID"
            },
            {
                "type": "radio_id_range",
                "pattern": "2340000-2349999",
                "passphrase": "fleet_pass_1",
                "description": "Range of radio IDs"
            },
            {
                "type": "callsign",
                "pattern": "W1ABC",
                "passphrase": "w1abc_pass",
                "description": "Exact callsign match"
            },
            {
                "type": "callsign_wild",
                "pattern": "W1*",
                "passphrase": "w1_prefix_pass",
                "description": "Wildcard callsign match"
            }
        ]
    },
    "access_rules": [
        {
            "type": "radio_id",
            "pattern": "1234567",
            "allow": true,
            "description": "Single radio allowed"
        },
        {
            "type": "radio_id_range",
            "pattern": "2340000-2349999",
            "allow": true,
            "description": "Fleet range allowed"
        },
        {
            "type": "callsign_wild",
            "pattern": "W1*",
            "allow": true,
            "description": "All W1 prefix allowed"
        },
        {
            "type": "radio_id_range",
            "pattern": "3000000-3999999",
            "allow": false,
            "description": "Blocked range"
        }
    ]
}
```

### Repeater Options
```json
"repeater_options": {
    "default": {
        "group_hangtime": 5,
        "slot_restrictions": false,
        "require_authentication": true,
        "allow_bridging": true,
        "metadata_required": ["callsign", "rxfreq", "txfreq", "location"]
    }
}
```

### Routing Rules
```json
"routing_rules": {
    "default": {
        "policy": "open",
        "allowed_tgs": [1, 2, 9],
        "denied_tgs": [],
        "route_all_to_tg": null,
        "isolated": false
    }
}
```

### Monitoring
```json
"monitoring": {
    "stats_interval": 60,
    "report_inactive": true,
    "inactive_timeout": 300
}
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the GNU GPLv3 License - see the LICENSE file for details.

## Acknowledgments

- Original HBlink3 by Cort Buffington, N0MJS
- The MMDVM and DMR community
