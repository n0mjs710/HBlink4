# Connecting to HBlink4

This guide explains how to connect repeaters and hotspots to an HBlink4 server.

## Required Information

To connect to an HBlink4 server, you need:

- **Server IP/Hostname**: The address of the HBlink4 server
- **Server Port**: Default is 62031 (IPv4) and/or 62032 (IPv6)
- **Repeater ID**: Your DMR radio ID (32-bit integer)
- **Passkey**: Authentication key (provided by server administrator)
- **Callsign**: Your amateur radio callsign
- **Location**: Station location
- **Frequencies**: TX and RX frequencies (if applicable)

## Repeater/Hotspot Configuration

### Basic Setup Steps

1. **Set connection mode to "Homebrew" or "HBlink"** in your software
2. **Enter server details**:
   - Server IP address or hostname
   - Port (usually 62031 for IPv4 or 62032 for IPv6)
3. **Enter your credentials**:
   - Your DMR radio ID
   - Passkey (provided by server administrator)
   - Callsign
   - Location
4. **Configure talkgroups** (if your software supports it)
5. **Save and restart** your repeater/hotspot software


```

**Contact your server administrator** to have your repeater added to their configuration.

### Firewall Requirements

Your repeater/hotspot needs to make outbound UDP connections to the server. Most home/office firewalls allow outbound traffic by default.

**Important**: Make sure your firewall's UDP session timeout is **longer** than your repeater's ping interval (typically 5-30 seconds). If the timeout is too short, your connection may become unstable.

Most firewalls handle this automatically, but if you experience frequent disconnections, check your firewall's UDP timeout settings.

## Dynamic Talkgroup Selection

Some repeater software (like Pi-Star, WPSD) allows you to configure which talkgroups you want to use. This is called **dynamic talkgroup subscription**.

### How It Works

- Your repeater can request specific talkgroups after connecting
- The server will only accept talkgroups that **both** you request **and** the server allows
- You can change your talkgroup selection without disconnecting

### Configuration Examples

**For MMDVM.ini or DMRGateway.ini:**

Add the `Options=` line to your HBlink/Homebrew server configuration section:

```ini
[DMR Network 1]
Enabled=1
Address=hblink.example.com
Port=62031
Password=your-passkey
Options=TS1=1,2,3;TS2=10,20,30
```

**Format:** `Options=TS1=tg1,tg2,tg3;TS2=tg4,tg5,tg6`
- List talkgroups for each timeslot separated by commas
- Separate TS1 and TS2 with a semicolon
- To disable a timeslot, leave it empty: `Options=TS1=;TS2=10,20`
- To use all allowed talkgroups, omit the `Options=` line entirely

**Pi-Star/WPSD:**

These platforms typically have a GUI field for talkgroups. Enter them in the same format:
```
TS1=1,2,3;TS2=10,20,30
```

### Examples

### Examples

**Example 1: Select a Subset of Talkgroups**

Server allows: TGs 1, 2, 3, 4, 5, 91, 310 on TS1 and TGs 10, 20, 30, 40, 50 on TS2

You configure in your repeater software:
- TS1: TGs 1, 2, 3
- TS2: TGs 10, 20

Result: You'll receive traffic for those specific talkgroups only.

**Example 2: Request Talkgroups Not Allowed**

Server allows: TGs 1, 2, 3 on TS1

You configure: TGs 1, 2, 3, 91 on TS1

Result: You'll only get TGs 1, 2, 3 (TG 91 is rejected because it's not in the server's allowed list)

**Example 3: Use All Talkgroups**

Simply don't configure specific talkgroups in your software - you'll automatically get all talkgroups the server allows for your repeater.

**Example 4: Disable a Timeslot**

You can configure one timeslot empty to disable it:
- TS1: (empty/no talkgroups)
- TS2: TGs 10, 20

Result: No traffic on TS1, only TS2 talkgroups active.

### When to Use Dynamic Selection

**Use dynamic talkgroup selection when:**
- You want a subset of available talkgroups
- Different talkgroups needed for different times/events  
- Limited repeater capacity
- Local users prefer specific talkgroups

**Don't configure it when:**
- You want all allowed talkgroups (it will happen automatically)

## Troubleshooting

### Connection Refused

**Problem**: Cannot connect to server

**Solutions**:
- Verify server IP address and port number
- Check that your internet connection is working
- Confirm the server is online (ask the administrator)
- Make sure your firewall allows outbound UDP connections

### Authentication Failed

**Problem**: Connection drops immediately after connecting

**Solutions**:
- Verify your passkey matches what the server administrator gave you
- Confirm your DMR radio ID is correct
- Check for typos in your callsign
- Contact the server administrator to verify your credentials

### No Audio/Traffic

**Problem**: Connected but no audio passing

**Solutions**:
- Verify you're configured for the correct talkgroups
- Check that your timeslot settings match (TS1 or TS2)
- If using dynamic talkgroup selection, make sure your requested talkgroups are allowed by the server
- Try transmitting to see if you can key up the repeater
- Ask on the talkgroup if anyone can hear you

### Talkgroup Selection Not Working

**Problem**: Configured specific talkgroups but still getting all traffic (or no traffic)

**Problem**: Configured specific talkgroups but still getting all traffic (or no traffic)

**Solutions**:
- Verify your software supports dynamic talkgroup selection
- Check that the talkgroups you configured are allowed by the server
- Try not configuring specific talkgroups to use all available
- Contact the server administrator to verify which talkgroups are allowed for your repeater

### Frequent Disconnections

**Problem**: Repeater keeps disconnecting and reconnecting

**Solutions**:
- Check your internet connection stability
- Verify your firewall's UDP timeout is long enough (should be longer than ping interval)
- Make sure your keepalive/ping interval is set correctly (5-30 seconds typical)
- Try connecting from a different network to rule out ISP issues
- Contact the server administrator - the server may be overloaded or having issues

## Getting Help

If you need assistance:

1. **Check your repeater/hotspot software logs** for error messages
2. **Contact your server administrator** - they can see detailed connection logs
3. **Check Pi-Star/WPSD forums** if using those platforms
4. **Review HBlink4 documentation**: https://github.com/n0mjs710/HBlink4

## Quick Reference

### Typical Connection Settings

| Setting | Typical Value |
|---------|--------------|
| Protocol Mode | Homebrew / HBlink |
| Server Port (IPv4) | 62031 |
| Server Port (IPv6) | 62032 |
| Ping Interval | 5-30 seconds |

### Required Credentials

- DMR Radio ID (your repeater/hotspot ID)
- Passkey (from server administrator)
- Callsign
- Location (optional but recommended)

### What to Give Your Server Administrator

When requesting access to a server, provide:
- Your DMR radio ID
- Your callsign
- Your location
- Desired talkgroups (if you have specific preferences)
- Whether you're running a repeater or hotspot
