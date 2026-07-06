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

> HBlink4 speaks the HomeBrew Protocol directly. These instructions target `MMDVMHost` and `DMRGateway` — the two stacks that send and parse this protocol natively. GUI wrappers built on top of them are out of scope.

### Basic Setup Steps

1. **Set the network type to "Homebrew" or "HBlink"** in `MMDVM.ini` or `DMRGateway.ini`
2. **Enter server details**:
   - `Address=` — server IP address or hostname
   - `Port=` — usually 62031 for IPv4 or 62032 for IPv6
3. **Enter your credentials**:
   - Your DMR radio ID
   - Password / passkey (provided by server administrator)
   - Callsign
   - Location
4. **Configure talkgroups** via `Options=` (see [Dynamic Talkgroup Selection](#dynamic-talkgroup-selection))
5. **Save and restart** `MMDVMHost` / `DMRGateway`

**Contact your server administrator** to have your repeater added to their configuration.

### Firewall Requirements

Your repeater/hotspot needs to make outbound UDP connections to the server. Most home/office firewalls allow outbound traffic by default.

**Important**: Make sure your firewall's UDP session timeout is **longer** than your repeater's ping interval (typically 5-30 seconds). If the timeout is too short, your connection may become unstable.

Most firewalls handle this automatically, but if you experience frequent disconnections, check your firewall's UDP timeout settings.

## Dynamic Talkgroup Selection

MMDVMHost and DMRGateway let you declare which talkgroups you want on each timeslot via an `Options=` line in the network configuration. This is called **dynamic talkgroup subscription** and is delivered to HBlink4 as the RPTO packet after login.

> This guide covers the syntax that goes directly into `MMDVMHost` / `DMRGateway` configuration. GUI wrappers that sit on top of those stacks are out of scope — consult their own documentation for where to enter the string.

### How It Works

- Your repeater can request specific talkgroups after connecting
- The server will only accept talkgroups that **both** you request **and** the server allows
- You can change your talkgroup selection without disconnecting

### Configuration

Add the `Options=` line to your HBlink/Homebrew server section in `MMDVM.ini` or `DMRGateway.ini`:

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

### Examples

**Example 1: Select a Subset of Talkgroups**

Server allows: TGs 1, 2, 3, 4, 5, 91, 310 on TS1 and TGs 10, 20, 30, 40, 50 on TS2

You configure in `MMDVM.ini` / `DMRGateway.ini`:
```ini
Options=TS1=1,2,3;TS2=10,20
```

Result: You'll receive traffic for those specific talkgroups only.

**Example 2: Request Talkgroups Not Allowed**

Server allows: TGs 1, 2, 3 on TS1

You configure: `Options=TS1=1,2,3,91`

Result: You'll only get TGs 1, 2, 3 (TG 91 is rejected because it's not in the server's allowed list)

**Example 3: Use All Talkgroups**

Omit the `Options=` line entirely — you'll automatically get every talkgroup the server allows for your repeater.

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

## Advanced: Slot / Talkgroup Translation and rf_src Override

> **Trusted repeaters only.** These extensions are honored only when the HBlink4 administrator has set `"trust": true` for your repeater. Non-trusted repeaters have the extra syntax silently ignored (the basic subscription still works).

The `Options=` string in `MMDVM.ini` / `DMRGateway.ini` accepts extended syntax that lets HBlink4 translate between **your local addressing** and the **network's addressing** — so your users can talk on local slot/tgid combinations that differ from what the wider network uses, without renumbering either side.

### Extended `Options=` grammar

In addition to the basic `TS1=tgids;TS2=tgids` form, each comma-separated entry may carry a translation clause:

```
TS1 = entry[,entry...]
TS2 = entry[,entry...]
SRC = radio_id

entry          = net_tgid[:local_slot[:local_tgid]]
net_tgid       = N  |  N-M   (range, inclusive)
local_slot     = 1 | 2 | *    (* = same as network slot)
local_tgid     = N | *        (* = same as matched network tgid)
```

- The `TS1=` / `TS2=` key always names the **network** slot.
- Entries without a colon are plain subscriptions (same as today).
- `SRC=` rewrites the rf_src on every outgoing group-voice packet from this repeater — useful for presenting a single "site radio" to the rest of the network.

### Quick examples (drop straight into `Options=`)

```ini
; Subscribe to network TG9 on TS1 (legacy, unchanged)
Options=TS1=9

; Swap slots: deliver network TS1/TG9 on your local TS2/TG9
Options=TS1=9:2:9

; Renumber: network TS1/TG9 is heard locally as TS1/TG32
Options=TS1=9:*:32

; Bring a whole range onto your TS2, preserving tgids
Options=TS1=3000-3200:2:*

; Range with an exception — TG3120 stays on TS1, rest go to TS2
Options=TS1=3000-3200:2:*,3120:1:3120

; Outbound rf_src override — everything you transmit appears to come from ID 9990001
Options=TS1=*;TS2=*;SRC=9990001
```

### Rules to keep in mind

- **Ranges** are expanded up to 10,000 tgids; larger ranges are rejected at parse time.
- **No wildcards on the network side** — use a specific TGID or a range. `*` as the network tgid or prefixes like `9*` are rejected.
- **Most-specific wins.** An exact TG in a range overrides the range for that TG (see TG3120 example above). The server logs a warning for any conflicting less-specific rule it drops.
- **`SRC=` applies only to group voice.** It's one-way — there is no reverse mapping, because group destinations don't carry return-address semantics.

For the full semantics (Link Control rewriting, collision handling, mid-stream RPTO behavior, operational notes) see **[dmrd_translation.md](dmrd_translation.md)**.

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

**Solutions**:
- Verify the `Options=` line is present and correctly formatted in `MMDVM.ini` / `DMRGateway.ini`
- Check that the talkgroups you configured are allowed by the server
- Try removing the `Options=` line entirely to get all available talkgroups
- Contact the server administrator to verify which talkgroups are allowed for your repeater

### Frequent Disconnections

**Problem**: Repeater keeps disconnecting and reconnecting

**Solutions**:
- Check your internet connection stability
- Verify your firewall's UDP timeout is long enough (should be longer than ping interval)
- Make sure your keepalive/ping interval is set correctly (5-30 seconds typical)
- Try connecting from a different network to rule out ISP issues
- Contact the server administrator - the server may be overloaded or having issues
- Ensure you are not connecting multiple devices with the same Radio ID

## Getting Help

If you need assistance:

1. **Check your MMDVMHost / DMRGateway logs** for error messages
2. **Contact your server administrator** - they can see detailed connection logs
3. **Review HBlink4 documentation**: https://github.com/n0mjs710/HBlink4

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
