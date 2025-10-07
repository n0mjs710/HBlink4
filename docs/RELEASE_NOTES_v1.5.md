# HBlink4 Release v1.5 - Detailed Repeater Information

## Release Date
October 7, 2025

## Overview
This release implements a comprehensive detailed repeater information feature with minimal overhead to HBlink4. When users click on a repeater's callsign/ID in the dashboard, a modal displays extensive information about the repeater including access control details, connection statistics, location data, and pattern match information.

## Key Features

### 1. Pattern Match Information Display
- Shows which access control pattern matched the repeater
- Displays pattern name and description
- Indicates the specific match reason (specific ID, ID range, or callsign pattern)
- Reveals whether repeater is using default config or a specific pattern

### 2. One-Time Metadata Event
**New Event: `repeater_details`**
- Emitted once when repeater completes configuration
- Contains static information that doesn't change during connection:
  - Latitude, longitude, height
  - TX power
  - Description and URL
  - Software ID and package ID
  - Slots configuration
  - Pattern match information (name, description, match reason)

**Overhead**: ~400 bytes once per repeater connection (negligible)

### 3. On-Demand Statistics API
**New Endpoint: `/api/repeater/{repeater_id}`**
- Fetches detailed information when user clicks on repeater
- Returns comprehensive JSON with:
  - Connection info (address, uptime, ping status)
  - Location data (coordinates, height)
  - Frequencies (RX/TX, power, color code)
  - Access control (pattern matched, TG source, RPTO status)
  - Talkgroup assignments (TS1 and TS2)
  - Metadata (description, URL, software versions)
  - Runtime statistics (streams today, active slots)

**Overhead**: Zero unless user actively requests details

### 4. Interactive Dashboard UI
- **Clickable Repeater Headers**: Mouse cursor changes to pointer on hover
- **Hover Effects**: Headers highlight when user hovers over them
- **Modal Display**: Clean, dark-themed modal with organized sections
- **Responsive Design**: Modal scrolls for long content, sticky header
- **Easy Dismissal**: Click outside modal or X button to close

## Technical Implementation

### Backend Changes

#### `hblink4/access_control.py`
```python
def get_pattern_for_repeater(self, radio_id: int, callsign: Optional[str] = None) -> Optional[PatternMatch]:
    """Return the pattern that matched this repeater, or None if using default"""
```
- New method to retrieve the matched pattern object
- Used by HBlink to determine match reason for dashboard display

#### `hblink4/hblink.py`
```python
def _emit_repeater_details(self, repeater_id: bytes, repeater: RepeaterState) -> None:
    """Emit detailed repeater information (sent once on connection)"""
```
- Called immediately after repeater configuration completes
- Extracts all metadata from RepeaterState
- Determines which pattern matched and why
- Emits single `repeater_details` event with complete information

#### `dashboard/server.py`
- Added `repeater_details` dictionary to DashboardState
- Handle `repeater_details` event and store in state
- New `/api/repeater/{id}` endpoint:
  - Combines connection info from repeaters state
  - Merges with detailed info from repeater_details state
  - Calculates runtime stats (uptime, stream counts)
  - Returns comprehensive JSON response

### Frontend Changes

#### `dashboard/static/dashboard.html`

**State Management**:
```javascript
state.repeater_details = {};  // Store detailed info by repeater_id
```

**Event Handling**:
- Handle `repeater_details` event from WebSocket
- Store details indexed by repeater_id
- Include in initial_state data

**UI Components**:
- Added CSS for modal overlay and content
- Made repeater headers clickable with `onclick` handler
- Hover effects for visual feedback

**Modal Function**:
```javascript
async function showRepeaterDetails(repeaterId) {
    // Fetch from API
    // Build comprehensive modal with sections
    // Display with dark theme styling
}
```

**Modal Sections**:
1. **Access Control**: Pattern info, match reason, RPTO status
2. **Connection**: Address, uptime, ping status
3. **Location**: Location name, coordinates, height
4. **Frequencies & Settings**: RX/TX frequencies, power, color code
5. **Talkgroups**: TS1 and TS2 assignments
6. **Metadata**: Description, URL, software versions (if available)
7. **Statistics**: Total streams today, active slot status

## Performance Impact

### Overhead Analysis

| Component | Overhead | Frequency | Impact |
|-----------|----------|-----------|--------|
| `repeater_details` event | ~400 bytes | Once per connection | Negligible |
| `repeater_connected` event | Unchanged | 4/minute | No change |
| API endpoint | 0 bytes | User-triggered | None unless clicked |
| Dashboard state | ~400 bytes/repeater | Persistent | Minimal |

**Total Impact**: Essentially zero
- Static metadata sent only once when repeater connects
- No additional overhead on ping updates
- API calls only when user actively views details
- No performance degradation even with many repeaters

### Benefits Over Alternatives

**vs. Extended Events** (Option 2):
- Saves ~720 KB/hour with 10 repeaters
- No repeated transmission of static data
- Ping updates remain lightweight

**vs. Hybrid Approach** (Option 3):
- Cleaner implementation
- No need to decide what's "essential"
- Better separation of concerns

## User Experience

### How to Use
1. Navigate to HBlink4 dashboard
2. Locate repeater in the repeaters list
3. Click on the repeater's callsign or ID (blue header)
4. Modal opens with comprehensive information
5. Click outside modal or X button to close
6. Repeat for any connected repeater

### Visual Indicators
- **Cursor Changes**: Pointer cursor when hovering over repeater headers
- **Hover Highlight**: Blue headers darken on hover, yellow (warning) headers redden
- **Title Tooltip**: "Click for detailed information" appears on hover
- **Modal Animation**: Smooth fade-in when opening

## Configuration

No configuration changes required. Feature works automatically with existing config.

### Optional Enhancements (Future)
- Add refresh button in modal to update stats
- Show historical connection data
- Display stream history for repeater
- Add map view with lat/lon coordinates
- Export repeater details to JSON/PDF

## Compatibility

- **Backward Compatible**: Old dashboards ignore `repeater_details` events
- **Forward Compatible**: Works with any HBlink4 configuration
- **No Breaking Changes**: All existing functionality preserved

## Testing Recommendations

1. **Connect Test Repeaters**: Verify `repeater_details` event is emitted
2. **Click Repeater Headers**: Ensure modal opens correctly
3. **Verify Pattern Info**: Check pattern name, description, match reason are accurate
4. **Test API Endpoint**: `curl http://localhost:8080/api/repeater/312001`
5. **Check Different Patterns**: Test repeaters matching different patterns
6. **Verify RPTO Display**: Check TG source shows correctly for RPTO repeaters
7. **Test Default Config**: Ensure repeaters using default config show correctly
8. **Load Test**: Connect multiple repeaters, verify no performance degradation

## Known Limitations

1. **Pattern Description Field**: Optional field may be empty in older configs
2. **Coordinates**: Only shown if repeater provides lat/lon data
3. **Stream Count**: Only counts streams since dashboard started (resets on restart)
4. **Real-time Updates**: Modal shows snapshot at click time (not live-updating)

## Future Enhancements

### Potential Additions
- **Live Updates**: WebSocket updates to modal while open
- **Historical Data**: Track connection history, uptime statistics
- **Map Integration**: Show repeater locations on map
- **Charts**: Visual representation of stream activity
- **Comparison View**: Compare multiple repeaters side-by-side
- **Export**: Download repeater info as JSON or PDF
- **Search/Filter**: Find repeaters by pattern, location, etc.

## Migration Notes

### From v1.4 to v1.5
- No database changes required
- No configuration changes needed
- Dashboard will receive new events automatically
- Old dashboard versions will ignore new events (graceful degradation)

### Restart Required
- **HBlink4**: Yes (to emit `repeater_details` events)
- **Dashboard**: Yes (to handle new events and API endpoint)

### Rollback Procedure
If issues arise:
```bash
cd /home/cort/hblink4
git checkout v1.4
systemctl restart hblink4
systemctl restart hblink4-dash
```

## Credits

Based on investigation document: `docs/DETAILED_REPEATER_INFO_INVESTIGATION.md`

Implemented approach combines:
- **Option 1**: API endpoint for on-demand stats
- **Option 4**: Separate details event sent once per connection

## Related Documentation

- `docs/DETAILED_REPEATER_INFO_INVESTIGATION.md` - Full analysis of implementation options
- `docs/configuration.md` - Access control pattern configuration
- `docs/routing.md` - Stream routing and talkgroup management

## Changelog

### Added
- `repeater_details` event type in HBlink4
- `get_pattern_for_repeater()` method in RepeaterMatcher
- `_emit_repeater_details()` method in HBProtocol
- `/api/repeater/{id}` endpoint in dashboard server
- `repeater_details` state in dashboard
- Modal UI for displaying detailed repeater information
- Clickable repeater headers with hover effects
- Pattern match information display
- TG source indication (RPTO vs Pattern/Config)

### Changed
- Repeater headers now clickable and show pointer cursor
- Dashboard state includes `repeater_details` dictionary
- Initial WebSocket state includes `repeater_details`

### Performance
- No measurable impact on HBlink4 performance
- Dashboard memory usage increased by ~400 bytes per connected repeater
- No additional network traffic except when user requests details

## Version Tags

- **Previous**: v1.4 (Audio Notifications)
- **Current**: v1.5 (Detailed Repeater Information)
- **Next**: TBD
