# Detailed Repeater Information Page - Investigation & Options

## Overview
This document investigates options for adding clickable repeater cards that open detailed information pages, with focus on minimizing overhead to HBlink4.

## Current Data Available

### Data HBlink4 Already Sends to Dashboard

From the `repeater_connected` event in `hblink.py` (lines 1125-1137):
```python
{
    'repeater_id': int,
    'callsign': str,
    'location': str,
    'address': str,  # IP:port
    'rx_freq': str,
    'tx_freq': str,
    'colorcode': str,
    'slot1_talkgroups': List[int],
    'slot2_talkgroups': List[int],
    'rpto_received': bool,
    'last_ping': float,
    'missed_pings': int
}
```

### Additional Data Available in RepeaterState (hblink.py lines 98-149)
This data is stored in HBlink4 but **NOT currently sent** to dashboard:
```python
{
    # Connection info
    'ip': str,
    'port': int,
    'connected': bool,
    'authenticated': bool,
    'last_ping': float,
    'ping_count': int,
    'missed_pings': int,
    'salt': int,
    'connection_state': str,  # 'login', 'config', 'connected'
    'last_rssi': int,
    'rssi_count': int,
    
    # Metadata from repeater
    'tx_power': bytes,
    'latitude': bytes,
    'longitude': bytes,
    'height': bytes,
    'description': bytes,
    'slots': bytes,
    'url': bytes,
    'software_id': bytes,
    'package_id': bytes,
    
    # Access control
    'slot1_talkgroups': set,
    'slot2_talkgroups': set,
    'rpto_received': bool,
    
    # Active streams
    'slot1_stream': StreamState,
    'slot2_stream': StreamState
}
```

### Pattern Match Information (from access_control.py)
This data determines WHY a repeater was allowed to connect:
```python
PatternMatch {
    'name': str,              # e.g., "KS-DMR Network"
    'description': str,       # e.g., "Repeaters in the KS-DMR network"
    'config': {
        'passphrase': str,
        'slot1_talkgroups': List[int],
        'slot2_talkgroups': List[int]
    },
    'ids': List[int],         # Specific IDs matched
    'id_ranges': List[Tuple], # ID ranges matched
    'callsigns': List[str]    # Callsign patterns matched
}
```

### Runtime Statistics (dashboard could track)
- Total packets received
- Total packets forwarded
- Stream history (stored in dashboard's last_heard)
- Connection uptime
- Ping latency (could be calculated from ping timing)

---

## Implementation Options

### Option 1: **On-Demand API Endpoint** (RECOMMENDED)
**Overhead**: MINIMAL - only when user clicks

**Implementation**:
1. Make repeater callsign/ID clickable in dashboard
2. Click opens modal/new page that fetches `/api/repeater/<id>`
3. HBlink4 receives HTTP request and builds response from current `RepeaterState`
4. Dashboard displays all available data

**Pros**:
- Zero overhead unless user actively requests data
- Can include ALL available RepeaterState data
- Simple to implement - no changes to event system
- Can include computed stats (connection duration, packet counts, etc.)
- Can show which pattern matched and why

**Cons**:
- Requires HTTP API endpoint in dashboard server
- Data is snapshot at request time (but could refresh)

**Data Flow**:
```
User Click → Dashboard → HTTP GET /api/repeater/312001 
→ Dashboard Server → Query HBlink4 State → Build Response
→ Dashboard → Display Modal/Page
```

**Example Response**:
```json
{
  "repeater_id": 312001,
  "callsign": "WA0EDA-R",
  "connection": {
    "ip": "192.168.1.100",
    "port": 54321,
    "connected_at": 1733600000.0,
    "uptime_seconds": 3600,
    "ping_count": 240,
    "missed_pings": 0,
    "last_ping": 1733603600.0,
    "connection_state": "connected",
    "rssi_average": -65
  },
  "location": {
    "location": "Overland Park, KS",
    "latitude": "38.9822",
    "longitude": "-94.6708",
    "height": "100"
  },
  "frequencies": {
    "rx_freq": "449.37500",
    "tx_freq": "444.37500",
    "tx_power": "50",
    "colorcode": "1",
    "slots": "2"
  },
  "access_control": {
    "matched_pattern": "KS-DMR Network",
    "pattern_description": "Repeaters in the KS-DMR network",
    "match_reason": "id_range: 312000-312099",
    "rpto_received": true,
    "slot1_talkgroups": [2, 9],
    "slot2_talkgroups": [3120],
    "talkgroups_source": "RPTO"  // or "pattern" or "default"
  },
  "metadata": {
    "description": "Kansas City Metro Repeater",
    "url": "http://kc-dmr.example.com",
    "software_id": "HBlink4",
    "package_id": "20250101"
  },
  "statistics": {
    "total_packets": 15234,
    "total_streams": 42,
    "active_slot1": false,
    "active_slot2": true
  }
}
```

---

### Option 2: **Extended Event Data**
**Overhead**: MODERATE - sends more data on every `repeater_connected` event

**Implementation**:
1. Add all RepeaterState fields to `repeater_connected` event
2. Dashboard stores in `state.repeaters[id]`
3. Click opens modal using cached data

**Pros**:
- No additional API needed
- Data always available instantly
- Dashboard has complete picture

**Cons**:
- Increases every `repeater_connected` event size (~2-3x)
- Sends data that may never be used
- Events sent on every ping status change
- More memory usage in dashboard

**Overhead Analysis**:
- Current event: ~200 bytes
- With full data: ~500-600 bytes
- Sent ~4x per minute per repeater (ping updates)
- 10 repeaters = 24 KB/minute = 1.4 MB/hour

---

### Option 3: **Hybrid - Enriched Events + On-Demand Details**
**Overhead**: LOW - small increase in events, details on-demand

**Implementation**:
1. Add only essential fields to `repeater_connected` event:
   - Pattern name and description
   - Lat/lon (for map display)
   - Connection uptime
2. Click fetches full details via API for rarely-used fields:
   - Software version, package ID
   - Full metadata
   - Detailed stats

**Pros**:
- Balanced approach
- Common data (pattern info, location) always available
- Detailed data fetched only when needed
- Moderate overhead increase

**Cons**:
- More complex implementation
- Need to decide what's "essential"

**Suggested Essential Fields** (adds ~100 bytes):
```python
{
    # ... existing fields ...
    'matched_pattern': str,      # "KS-DMR Network"
    'pattern_description': str,  # "Repeaters in the KS-DMR network"
    'match_reason': str,         # "id_range: 312000-312099"
    'latitude': str,
    'longitude': str,
    'connection_uptime': int,    # seconds
    'tx_power': str
}
```

---

### Option 4: **Separate Details Event**
**Overhead**: MINIMAL - only sent once per connection

**Implementation**:
1. Emit new `repeater_details` event ONCE when repeater connects
2. Send full metadata in this event
3. Dashboard caches details separately
4. `repeater_connected` events remain lightweight for ping updates

**Pros**:
- No overhead on ping updates
- Full details available after connection
- Clean separation of concerns
- Dashboard can refresh details on demand

**Cons**:
- New event type to implement
- Details not refreshed unless requested
- Slightly more complex event handling

**Event Structure**:
```python
# Sent once on connection
self._events.emit('repeater_details', {
    'repeater_id': int,
    'latitude': str,
    'longitude': str,
    'height': str,
    'tx_power': str,
    'description': str,
    'url': str,
    'software_id': str,
    'package_id': str,
    'matched_pattern': str,
    'pattern_description': str,
    'match_reason': str
})

# Continue using lightweight repeater_connected for updates
```

---

## Overhead Comparison

| Option | Per-Event Overhead | Frequency | Total Overhead (10 rpts) |
|--------|-------------------|-----------|--------------------------|
| Current | 200 bytes | 4/min | 8 KB/min (480 KB/hr) |
| Option 1 (API) | 0 bytes | On-demand | ~0 KB/hr (unless clicked) |
| Option 2 (Full) | +300 bytes | 4/min | +12 KB/min (+720 KB/hr) |
| Option 3 (Hybrid) | +100 bytes | 4/min | +4 KB/min (+240 KB/hr) |
| Option 4 (Details Event) | +300 bytes | 1/connection | Negligible |

---

## Recommended Approach

### **Combination of Option 1 + Option 4** (Best of Both)

**Rationale**:
1. Send `repeater_details` event ONCE on connection with full metadata + pattern info
2. Keep `repeater_connected` lightweight for ping updates
3. Dashboard caches details and displays on click
4. Add HTTP API endpoint for "refresh details" button in case user wants latest data

**Benefits**:
- Minimal overhead (one-time per connection)
- All data available in dashboard without API call
- Option to refresh if needed
- Clean separation of static metadata vs dynamic status

**Implementation Steps**:

### Step 1: Add `repeater_details` event (hblink.py)
```python
def _handle_config(self, repeater_id: bytes, data: bytes, addr: PeerAddress):
    # ... existing code ...
    
    # After repeater is configured, emit detailed info once
    self._emit_repeater_details(repeater_id, repeater)
    
    # Keep existing repeater_connected event lightweight
    self._events.emit('repeater_connected', {
        # ... existing fields (lightweight)
    })

def _emit_repeater_details(self, repeater_id: bytes, repeater: RepeaterState):
    """Emit detailed repeater information (sent once on connection)"""
    # Get pattern match info
    try:
        pattern = self._matcher.get_pattern_for_repeater(
            int.from_bytes(repeater_id, 'big'),
            repeater.callsign.decode().strip() if repeater.callsign else None
        )
        pattern_name = pattern.name if pattern else "Default"
        pattern_desc = pattern.description if pattern else "Using default configuration"
        # Determine which match type succeeded
        if pattern and int.from_bytes(repeater_id, 'big') in pattern.ids:
            match_reason = f"specific_id: {int.from_bytes(repeater_id, 'big')}"
        elif pattern and any(start <= int.from_bytes(repeater_id, 'big') <= end 
                            for start, end in pattern.id_ranges):
            match_reason = f"id_range: {pattern.id_ranges[0]}"
        elif pattern and repeater.callsign:
            match_reason = f"callsign: {repeater.callsign.decode().strip()}"
        else:
            match_reason = "default"
    except:
        pattern_name = "Unknown"
        pattern_desc = ""
        match_reason = "unknown"
    
    self._events.emit('repeater_details', {
        'repeater_id': int.from_bytes(repeater_id, 'big'),
        'latitude': repeater.latitude.decode().strip() if repeater.latitude else '',
        'longitude': repeater.longitude.decode().strip() if repeater.longitude else '',
        'height': repeater.height.decode().strip() if repeater.height else '',
        'tx_power': repeater.tx_power.decode().strip() if repeater.tx_power else '',
        'description': repeater.description.decode().strip() if repeater.description else '',
        'url': repeater.url.decode().strip() if repeater.url else '',
        'software_id': repeater.software_id.decode().strip() if repeater.software_id else '',
        'package_id': repeater.package_id.decode().strip() if repeater.package_id else '',
        'matched_pattern': pattern_name,
        'pattern_description': pattern_desc,
        'match_reason': match_reason,
        'slots': repeater.slots.decode().strip() if repeater.slots else ''
    })
```

### Step 2: Add method to RepeaterMatcher (access_control.py)
```python
def get_pattern_for_repeater(self, radio_id: int, callsign: Optional[str] = None) -> Optional[PatternMatch]:
    """Return the pattern that matched this repeater, or None if default"""
    self._check_blacklist(radio_id, callsign)
    for pattern in self.patterns:
        if self._match_pattern(radio_id, callsign, pattern):
            return pattern
    return None
```

### Step 3: Handle in dashboard (dashboard.html)
```javascript
// Add to state
state.repeater_details = {};  // Store detailed info by repeater_id

// Handle event
case 'repeater_details':
    state.repeater_details[data.repeater_id] = data;
    break;

// Make callsign clickable
function updateRepeaters(repeaters) {
    // ... in card HTML ...
    `<div class="repeater-header" onclick="showRepeaterDetails(${r.repeater_id})">
        📶 ${r.callsign || 'Unknown'} (${r.repeater_id})
    </div>`
}

function showRepeaterDetails(repeaterId) {
    const repeater = state.repeaters[repeaterId];
    const details = state.repeater_details[repeaterId];
    
    // Show modal with combined data from both sources
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <h2>Repeater Details: ${repeater.callsign} (${repeater.repeater_id})</h2>
            
            <section>
                <h3>Connection</h3>
                <table>
                    <tr><td>Address:</td><td>${repeater.address}</td></tr>
                    <tr><td>Connected:</td><td>${formatUptime(repeater.connected_at)}</td></tr>
                    <tr><td>Last Ping:</td><td>${formatTime(repeater.last_ping)}</td></tr>
                    <tr><td>Missed Pings:</td><td>${repeater.missed_pings}</td></tr>
                </table>
            </section>
            
            <section>
                <h3>Access Control</h3>
                <table>
                    <tr><td>Pattern:</td><td><strong>${details?.matched_pattern}</strong></td></tr>
                    <tr><td>Description:</td><td>${details?.pattern_description}</td></tr>
                    <tr><td>Match Reason:</td><td>${details?.match_reason}</td></tr>
                    <tr><td>RPTO Received:</td><td>${repeater.rpto_received ? 'Yes' : 'No'}</td></tr>
                </table>
            </section>
            
            <section>
                <h3>Location & Power</h3>
                <table>
                    <tr><td>Location:</td><td>${repeater.location}</td></tr>
                    <tr><td>Coordinates:</td><td>${details?.latitude}, ${details?.longitude}</td></tr>
                    <tr><td>Height:</td><td>${details?.height}</td></tr>
                    <tr><td>TX Power:</td><td>${details?.tx_power}W</td></tr>
                </table>
            </section>
            
            <section>
                <h3>Frequencies</h3>
                <table>
                    <tr><td>RX:</td><td>${repeater.rx_freq} MHz</td></tr>
                    <tr><td>TX:</td><td>${repeater.tx_freq} MHz</td></tr>
                    <tr><td>Color Code:</td><td>${repeater.colorcode}</td></tr>
                    <tr><td>Slots:</td><td>${details?.slots}</td></tr>
                </table>
            </section>
            
            <section>
                <h3>Talkgroups</h3>
                <p>Source: ${repeater.rpto_received ? 'RPTO (Repeater)' : 'Config/Pattern'}</p>
                <table>
                    <tr><td>TS1:</td><td>${repeater.slot1_talkgroups.join(', ')}</td></tr>
                    <tr><td>TS2:</td><td>${repeater.slot2_talkgroups.join(', ')}</td></tr>
                </table>
            </section>
            
            <section>
                <h3>Metadata</h3>
                <table>
                    <tr><td>Description:</td><td>${details?.description}</td></tr>
                    <tr><td>URL:</td><td><a href="${details?.url}">${details?.url}</a></td></tr>
                    <tr><td>Software:</td><td>${details?.software_id}</td></tr>
                    <tr><td>Package:</td><td>${details?.package_id}</td></tr>
                </table>
            </section>
            
            <button onclick="this.parentElement.parentElement.remove()">Close</button>
        </div>
    `;
    document.body.appendChild(modal);
}
```

---

## Summary & Next Steps

**Recommended**: Option 1 + Option 4 combination
- Minimal overhead (one event per connection)
- All data available in dashboard
- Clean implementation
- Can add API endpoint later if needed

**Overhead**: 
- One-time ~400-byte event per repeater connection
- Negligible impact on HBlink4 performance
- No ongoing overhead for ping updates

**Would you like me to implement this approach?** If so, I can:
1. Add `get_pattern_for_repeater()` method to `access_control.py`
2. Add `_emit_repeater_details()` method to `hblink.py`
3. Update dashboard to handle `repeater_details` event
4. Make repeater cards clickable with modal display
5. Style the modal for clean presentation

Let me know if you'd like to proceed, or if you'd prefer a different option!
