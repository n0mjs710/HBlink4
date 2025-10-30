# HBlink4 Integration Guide

This document describes how to use HBlink4 as a module in your own applications for advanced DMR routing and control.

For basic installation and setup, see the main [README](../readme.md).

## Overview

HBlink4 is designed to be modular, allowing you to access repeater metadata, connection states, and control the routing of DMR traffic. The main interface is through the `HBProtocol` class.

## Core Classes

### HBProtocol

The main protocol handler class that manages repeater connections and DMR traffic.

```python
from hblink4.hblink import HBProtocol
from twisted.internet import reactor

protocol = HBProtocol()
```

### RepeaterState

Each connected repeater is represented by a `RepeaterState` object containing metadata about the connection.

#### Available Properties

| Property | Type | Description |
|----------|------|-------------|
| `ip` | str | Repeater's IP address |
| `port` | int | Repeater's UDP port |
| `radio_id` | bytes | Repeater's DMR ID (4 bytes) |
| `connection_state` | str | Current connection state ('login', 'config', 'connected') |
| `last_ping` | float | Timestamp of last ping received |
| `ping_count` | int | Number of pings received |
| `missed_pings` | int | Number of consecutive missed pings |
| `description` | bytes | Repeater's description from config |
| `slots` | bytes | Supported timeslots |
| `url` | bytes | Repeater's URL |
| `software_id` | bytes | Repeater's software identifier |
| `package_id` | bytes | Repeater's package identifier |

#### Accessing Repeater States

The HBProtocol maintains a dictionary of all connected repeaters:

```python
# Access repeater by ID
repeater = protocol._repeaters[radio_id]  # radio_id is bytes

# Iterate all repeaters
for radio_id, repeater in protocol._repeaters.items():
    print(f"Repeater {int.from_bytes(radio_id, 'big')}:")
    print(f"  State: {repeater.connection_state}")
    print(f"  Address: {repeater.ip}:{repeater.port}")
```

## Integrating with Your Application

### Complete Application Example

```python
#!/usr/bin/env python3
import sys
import json
import signal
from pathlib import Path
from twisted.internet import reactor
from hblink4.hblink import HBProtocol, CONFIG

class MyDMRApplication:
    def __init__(self, config_file: str):
        # Load configuration
        self.load_config(config_file)
        
        # Initialize HBlink4
        self.protocol = HBProtocol()
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.handle_shutdown)
        signal.signal(signal.SIGTERM, self.handle_shutdown)
        
        # Set up UDP listeners (dual-stack)
        # IPv4 listener
        if CONFIG['global'].get('bind_ipv4'):
            self.port_v4 = reactor.listenUDP(
                CONFIG['global']['port_ipv4'],
                self.protocol,
                interface=CONFIG['global']['bind_ipv4']
            )
        
        # IPv6 listener
        if CONFIG['global'].get('bind_ipv6') and not CONFIG['global'].get('disable_ipv6', False):
            self.port_v6 = reactor.listenUDP(
                CONFIG['global']['port_ipv6'],
                self.protocol,
                interface=CONFIG['global']['bind_ipv6']
            )
        
        # Set up periodic status check
        reactor.callLater(60, self.check_repeater_status)
        
    def load_config(self, config_file: str):
        """Load the HBlink configuration file"""
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                # Update the global CONFIG used by HBlink
                CONFIG.update(config)
        except Exception as e:
            print(f"Error loading config: {e}")
            sys.exit(1)
    
    def handle_shutdown(self, signum, frame):
        """Handle shutdown signals"""
        print("Shutting down...")
        # Let HBlink send disconnect messages
        self.protocol.cleanup()
        # Stop the reactor
        reactor.stop()
    
    def start(self):
        """Start the application"""
        bind_info = []
        if CONFIG['global'].get('bind_ipv4'):
            bind_info.append(f"{CONFIG['global']['bind_ipv4']}:{CONFIG['global']['port_ipv4']}")
        if CONFIG['global'].get('bind_ipv6') and not CONFIG['global'].get('disable_ipv6', False):
            bind_info.append(f"[{CONFIG['global']['bind_ipv6']}]:{CONFIG['global']['port_ipv6']}")
        print(f"Starting application on {', '.join(bind_info)}")
        # Start the Twisted reactor
        reactor.run()

def main():
    if len(sys.argv) != 2:
        print("Usage: mydmr.py /path/to/config.json")
        sys.exit(1)
        
    app = MyDMRApplication(sys.argv[1])
    app.start()

if __name__ == '__main__':
    main()
```

### Basic Usage Example

```python
from twisted.internet import reactor
from hblink4.hblink import HBProtocol, CONFIG

class MyDMRApplication:
    def __init__(self):
        # Initialize HBlink4
        self.protocol = HBProtocol()
        
        # Set up dual-stack UDP listeners
        reactor.listenUDP(
            CONFIG['global']['port_ipv4'],
            self.protocol,
            interface=CONFIG['global']['bind_ipv4']
        )
        
        # Set up periodic status check
        reactor.callLater(60, self.check_repeater_status)

    def check_repeater_status(self):
        """Example of accessing repeater information"""
        for radio_id, repeater in self.protocol._repeaters.items():
            if repeater.connection_state == 'yes':
                self.handle_active_repeater(radio_id, repeater)
        
        # Schedule next check
        reactor.callLater(60, self.check_repeater_status)

    def handle_active_repeater(self, radio_id: bytes, repeater):
        """Example handler for connected repeaters"""
        repeater_id = int.from_bytes(radio_id, 'big')
        print(f"Active repeater {repeater_id}:")
        print(f"  Last ping: {repeater.last_ping}")
        print(f"  Description: {repeater.description.decode('utf-8', errors='ignore')}")
```

### Event Hooks

To be notified of repeater events, you can subclass HBProtocol:

```python
class MyHBProtocol(HBProtocol):
    def handle_repeater_connection(self, radio_id: bytes, repeater):
        """Called when a repeater completes connection"""
        super().handle_repeater_connection(radio_id, repeater)
        # Your custom connection handling here
        
    def handle_dmr_data(self, data: bytes, addr):
        """Called for each DMR data packet"""
        super().handle_dmr_data(data, addr)
        # Your custom DMR routing logic here
```

## Common Integration Tasks

### Getting Connected Repeater Count
```python
connected_count = sum(1 for r in protocol._repeaters.values() 
                     if r.connection_state == 'yes')
```

### Finding a Repeater by IP
```python
def find_repeater_by_ip(protocol, ip_address: str):
    for repeater in protocol._repeaters.values():
        if repeater.ip == ip_address:
            return repeater
    return None
```

### Monitoring Connection States
```python
def get_repeater_states(protocol):
    states = {'connected': [], 'config': [], 'login': []}
    for radio_id, repeater in protocol._repeaters.items():
        states[repeater.connection_state].append(int.from_bytes(radio_id, 'big'))
    return states
```

## Best Practices

1. **Read-Only Access**: Avoid directly modifying the `_repeaters` dictionary or repeater states. Instead, use protocol methods to interact with repeaters.

2. **State Handling**: Always check `connection_state` before acting on a repeater.

3. **Error Handling**: Wrap repeater access in try/except blocks as repeaters may disconnect at any time.

4. **Event-Driven**: Use the event hooks in preference to polling when possible.

5. **Thread Safety**: HBlink4 uses Twisted's event loop. Ensure all access is done through Twisted's thread-safe mechanisms if operating from other threads.

## Limitations

1. Direct modification of routing rules must be done via configuration before startup (if DMRD is not subclassed)
2. Some repeater metadata may contain non-UTF8 characters
3. All radio IDs are handled as raw bytes - remember to convert to int for display/logging

## Advanced Topics

### Custom Packet Processing
To implement custom DMR packet processing, subclass HBProtocol and override:

- `datagramReceived`: For raw packet access
- `handle_dmr_data`: For DMR data packet processing
- Other specific handlers like `handle_repeater_login`, etc.

### Graceful Shutdown
If implementing custom shutdown logic, ensure you:

1. Call protocol.cleanup() to send disconnect messages
2. Allow time for messages to be sent (0.5s recommended)
3. Stop the reactor
