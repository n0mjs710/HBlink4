# Dashboard Transport Configuration

## Overview

HBlink4 supports **two transport options** for dashboard communication:

1. **Unix Socket** (Recommended for same-host deployment)
2. **TCP** (Required for remote dashboard)

Both HBlink and the Dashboard must be configured to use the **same transport**.

---

## Transport Comparison

| Feature | Unix Socket | TCP |
|---------|-------------|-----|
| **Performance** | Fastest (~0.5-1μs) | Fast (~5-15μs) |
| **Reliability** | Guaranteed delivery | Guaranteed delivery |
| **Connection state** | ✅ Yes | ✅ Yes |
| **Remote capable** | ❌ Same host only | ✅ Yes |
| **IPv6 support** | N/A | ✅ Yes |
| **Security** | Filesystem perms | ⚠️ Network exposed |
| **Use case** | Local dashboard (best) | Remote dashboard |

---

## Configuration

### HBlink Configuration (`config/config.json`)

```json
{
    "global": {
        "dashboard": {
            "enabled": true,
            "transport": "unix",
            "host": "127.0.0.1",
            "port": 8765,
            "unix_socket": "/tmp/hblink4.sock",
            "ipv6": false,
            "buffer_size": 65536
        }
    }
}
```

### Dashboard Configuration (`dashboard/config.json`)

```json
{
    "event_receiver": {
        "transport": "unix",
        "host": "127.0.0.1",
        "port": 8765,
        "unix_socket": "/tmp/hblink4.sock",
        "ipv6": false,
        "buffer_size": 65536
    }
}
```

**IMPORTANT**: Both configs must use the **same transport** and **same connection details**.

---

## Transport Options

### Option 1: Unix Socket (Recommended for Local)

**Best for**: Dashboard running on same host as HBlink

**Advantages**:
- ✅ Fastest performance (~50% faster than TCP/UDP)
- ✅ Reliable (guaranteed delivery, ordered)
- ✅ Automatic reconnection
- ✅ Filesystem-based security (chmod/chown)
- ✅ Can't be accessed over network

**Configuration**:
```json
{
    "transport": "unix",
    "unix_socket": "/tmp/hblink4.sock"
}
```

**Socket file permissions**: Automatically set to `0660` (user + group access)

**Cleanup**: Socket file automatically removed on dashboard startup

---

### Option 2: TCP (Required for Remote)

**Best for**: Dashboard running on different server than HBlink

**Advantages**:
- ✅ Remote dashboard capability
- ✅ Reliable (guaranteed delivery, ordered)
- ✅ Automatic reconnection
- ✅ Connection state tracking
- ✅ IPv4 and IPv6 support

**Configuration (IPv4)**:
```json
{
    "transport": "tcp",
    "host": "192.168.1.100",
    "port": 8765,
    "ipv6": false
}
```

**Configuration (IPv6)**:
```json
{
    "transport": "tcp",
    "host": "2001:db8::1",
    "port": 8765,
    "ipv6": true
}
```

**Security Warning**: TCP exposes dashboard events on the network. Use firewall rules to restrict access:
```bash
# Allow only localhost
sudo ufw allow from 127.0.0.1 to any port 8765

# Allow specific IP
sudo ufw allow from 192.168.1.50 to any port 8765
```

**Future**: TLS/SSL encryption support planned for secure remote dashboards.
---

## Connection State Awareness

**TCP and Unix Socket** provide connection state tracking:

### HBlink Side
- Attempts connection on startup
- Auto-reconnects every 10 seconds if disconnected
- Logs connection status changes
- Drops events if dashboard is offline (prevents blocking)

### Dashboard Side
- Logs when HBlink connects
- Logs when HBlink disconnects
- Multiple HBlink instances can connect simultaneously (TCP only)

---

## Deployment Scenarios

### Scenario 1: Dashboard on Same Server (Recommended)

**Use Unix socket for best performance**:

```json
// HBlink config
{
    "dashboard": {
        "enabled": true,
        "transport": "unix",
        "unix_socket": "/tmp/hblink4.sock"
    }
}

// Dashboard config
{
    "event_receiver": {
        "transport": "unix",
        "unix_socket": "/tmp/hblink4.sock"
    }
}
```

**Start order**: Dashboard must start first (creates socket), then HBlink connects.

---

### Scenario 2: Dashboard on Different Server

**Use TCP for remote access**:

```json
// HBlink config (on server A: 192.168.1.100)
{
    "dashboard": {
        "enabled": true,
        "transport": "tcp",
        "host": "192.168.1.200",  // Dashboard server IP
        "port": 8765
    }
}

// Dashboard config (on server B: 192.168.1.200)
{
    "event_receiver": {
        "transport": "tcp",
        "host": "0.0.0.0",  // Listen on all interfaces
        "port": 8765
    }
}
```

**Firewall**: Open port 8765 on dashboard server:
```bash
sudo ufw allow 8765/tcp
```

---

### Scenario 3: Multiple HBlink Instances, One Dashboard

**Use TCP** (Unix socket supports only one client):

```json
// Dashboard config (one instance)
{
    "event_receiver": {
        "transport": "tcp",
        "host": "0.0.0.0",
        "port": 8765
    }
}

// HBlink config (multiple instances, same transport)
{
    "dashboard": {
        "enabled": true,
        "transport": "tcp",
        "host": "127.0.0.1",  // Or remote dashboard IP
        "port": 8765
    }
}
```

**Note**: Dashboard will merge events from all HBlink instances.

---

## Troubleshooting

### "Connection refused" errors

**Symptoms**: HBlink logs show "Dashboard not available yet" or "Connection refused"

**Causes**:
1. Dashboard not running
2. Wrong transport configuration (mismatch between HBlink and Dashboard)
3. Wrong host/port/socket path
4. Firewall blocking connection (TCP)
5. Socket file permissions (Unix socket)

**Solution**:
```bash
# Check dashboard is running
ps aux | grep dashboard

# Check dashboard is listening
netstat -tlnp | grep 8765  # TCP
netstat -xlnp | grep hblink4  # Unix socket

# Check HBlink config matches dashboard config
diff <(jq .global.dashboard config/config.json) \
     <(jq .event_receiver dashboard/config.json)

# Check firewall (TCP)
sudo ufw status

# Check socket permissions (Unix)
ls -l /tmp/hblink4.sock
```

### Dashboard shows stale data

**Symptoms**: Active calls shown after they ended, repeaters shown as connected after disconnect

**Causes**:
1. Dashboard lost connection to HBlink (TCP/Unix only)
2. HBlink crashed but dashboard still running

**Solution**:
- Restart dashboard (clears stale state)
- Check HBlink is running: `ps aux | grep hblink`
- Check connection state in dashboard logs

### High CPU usage

**Symptoms**: HBlink or Dashboard consuming high CPU

**Causes**:
1. Rapid reconnection attempts (TCP/Unix)
2. Large buffer_size causing memory thrashing
3. Event processing bottleneck

**Solution**:
```json
{
    "buffer_size": 32768  // Reduce from 65536
}
```

### Socket file exists on dashboard startup

**Symptoms**: Dashboard fails to start with "Address already in use"

**Cause**: Previous dashboard instance didn't clean up socket file

**Solution**:
```bash
rm /tmp/hblink4.sock
```

The dashboard automatically removes stale socket files, but manual cleanup may be needed if dashboard crashed.

---

## Performance Notes

### Event Volume (Typical Setup: 10 repeaters, 5 concurrent calls)
- ~7 events/second
- ~55KB/minute bandwidth
- Negligible CPU impact (<0.01%)

### Latency Comparison (Localhost)
- Unix socket: **0.5-1μs** (fastest)
- TCP: **5-15μs**

Both transports are **fast enough** for real-time DMR monitoring (60ms packet spacing).

### Reconnection Behavior (TCP/Unix)
- Retry interval: 10 seconds
- No event queuing during disconnect (events are dropped)
- Dashboard resyncs on reconnect via state snapshots

---

## Security Considerations

### Unix Socket
- ✅ **Safest**: Cannot be accessed over network
- ✅ Filesystem permissions control access
- ✅ Recommended for production local deployments

### TCP (Localhost Only)
- ⚠️ Exposed on network interface
- ⚠️ No encryption (plaintext JSON)
- ⚠️ Use firewall to restrict to localhost

### TCP (Remote Dashboard)
- ⚠️ **Risk**: Network traffic is unencrypted
- ⚠️ **Risk**: Anyone on network can see DMR activity
- ⚠️ Use VPN or SSH tunnel for production
- ⚠️ TLS support planned for future release

**Recommended remote setup** (secure):
```bash
# On dashboard server, create SSH tunnel
ssh -L 8765:localhost:8765 hblink-server

# Dashboard connects to localhost:8765
# Traffic encrypted via SSH
```

---

## Advanced Configuration

### Custom Buffer Size
```json
{
    "buffer_size": 32768  // Smaller = more aggressive event dropping
                          // Larger = more buffering (uses more memory)
}
```

**Recommendation**: Keep default (65536 bytes) unless experiencing issues.

### IPv6 Support (TCP)
```json
{
    "transport": "tcp",
    "host": "::1",  // IPv6 localhost
    "port": 8765,
    "ipv6": true
}
```

### Disable Dashboard
```json
{
    "dashboard": {
        "enabled": false  // Zero overhead, no socket created
    }
}
```

---

## Logging

### HBlink Logs
```
📡 TCP event emitter initialized for 192.168.1.200:8765
✅ Connected to dashboard (tcp)
⚠️ Dashboard connection lost: Connection reset by peer
```

### Dashboard Logs
```
📡 Listening for HBlink4 events via Unix socket at /tmp/hblink4.sock
✅ HBlink4 connected via Unix socket
⚠️ HBlink4 Unix socket connection lost
```

Set log level to DEBUG for more detail:
```json
{
    "logging": {
        "console_level": "DEBUG"
    }
}
```
