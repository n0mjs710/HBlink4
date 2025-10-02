# DMR Link Control (LC) Extraction

## Overview

HBlink4 extracts DMR Link Control (LC) information from voice frames to provide detailed metadata about transmissions. This information is available in addition to the basic packet headers.

**Implementation:** HBlink4 uses the mature and proven **dmr_utils3** library for all LC decoding. This provides reliable FEC (Forward Error Correction), BPTC decoding, and proper handling of the complex DMR encoding schemes.

## What is Link Control?

Link Control is metadata embedded in DMR voice frames that describes the call parameters:

- **Source and Destination IDs**: Who is calling whom
- **Call Type**: Group call vs. private call
- **Emergency Status**: Whether this is an emergency transmission
- **Privacy**: Whether privacy/encryption is enabled
- **Service Options**: Other call features

## LC Extraction Methods

### Voice Header/Terminator LC

Voice sync frames (headers and terminators) contain **full LC information** (72 bits):

- Located after the sync pattern in the payload
- Provides complete call metadata
- Most reliable source of LC information
- Extracted automatically when sync frames are received

### Embedded LC (Implemented with Optimization)

Voice frames can have LC embedded across multiple frames:

- Spreads LC across frames 1-4 of a superframe (voice frames B-E)
- Useful if the header frame is missed
- Requires accumulation across multiple frames
- FEC already applied by the repeater before transmission to HBlink4
- **Optimization**: Only extracted when voice header was missed (avoids processing overhead)
- **Note**: FEC will need to be recalculated when modifying LC for stream forwarding

## LC Structure

The Link Control structure (9 bytes / 72 bits):

```
Byte 0:
  Bits 7-2: FLCO (Full Link Control Opcode)
  Bits 1-0: FID MSB (Feature ID, upper 2 bits)

Byte 1:
  Bits 7-2: FID LSB (Feature ID, lower 6 bits)
  Bits 1-0: Service Options MSB

Byte 2:
  Bits 7-2: Service Options LSB
  Bits 1-0: Destination ID MSB

Bytes 3-5:
  Destination ID (22 remaining bits)

Bytes 6-8:
  Source ID (24 bits)

(CRC-16 follows but is not included in these 9 bytes)
```

## Talker Alias Extraction

### What is Talker Alias?

Talker alias provides a human-readable name or callsign that can be displayed on receiving radios. It's more user-friendly than just showing a numeric DMR ID.

### Transmission Format

Talker alias is transmitted across **4 LC frames**:

1. **Header (FLCO=4)**: Contains format type and length
2. **Block 1 (FLCO=5)**: 7 bytes of alias data
3. **Block 2 (FLCO=6)**: 7 bytes of alias data  
4. **Block 3 (FLCO=7)**: 7 bytes of alias data

Total capacity: 28 bytes (7 bytes per frame × 4 frames)

### Encoding Formats

| Format | Description | Use Case |
|--------|-------------|----------|
| 0 | 7-bit ASCII | Standard English text |
| 1 | ISO-8859-1 (Latin-1) | European languages with accents |
| 2 | UTF-8 | International characters |
| 3 | UTF-16BE | Unicode (less common) |

### Extraction Process

HBlink4 automatically collects talker alias data:

```python
# Detection
if lc.is_talker_alias_header:
    # Extract format and length from header
    format_type = lc.service_options & 0x03
    length = (lc.service_options >> 2) & 0x3F

elif lc.is_talker_alias_block:
    # Extract 7 bytes from dst_id + src_id + fid fields
    block_num = lc.flco - 4  # 1, 2, or 3
```

### Example

Transmitting "N0MJS Cort":

```
Header (FLCO=4): Format=0 (7-bit), Length=11
  Data: "N0MJS C"
Block 1 (FLCO=5): "ort\x00\x00\x00\x00"
Block 2 (FLCO=6): Not needed
Block 3 (FLCO=7): Not needed
```

Result: "N0MJS Cort" displayed on receiving radios

### Usage in Code

```python
# In stream state
current_stream.talker_alias          # Decoded alias string
current_stream.talker_alias_format   # Encoding format (0-3)
current_stream.talker_alias_length   # Expected length
current_stream.talker_alias_blocks   # Collected blocks {0: data, 1: data, ...}
```

### Logging

When talker alias is received:

```
DEBUG - Talker alias header: format=0, length=11
DEBUG - Talker alias block 1 received
INFO - Talker alias: "N0MJS Cort" (format=0, blocks=[0, 1])
```



| FLCO | Type | Description |
|------|------|-------------|
| 0 | Group Voice | Group call (most common) |
| 3 | Private Voice | Private call (unit-to-unit) |
| 4 | Talker Alias Header | Talker alias header with format/length |
| 5 | Talker Alias Block 1 | Talker alias block 1 (7 bytes) |
| 6 | Talker Alias Block 2 | Talker alias block 2 (7 bytes) |
| 7 | Talker Alias Block 3 | Talker alias block 3 (7 bytes) |

## Service Options

| Bit | Meaning |
|-----|---------|
| 7 | Emergency |
| 6 | Privacy (encryption) |
| 5 | Reserved |
| 4 | Broadcast |
| 3 | OVCM (Open Voice Call Mode) |
| 2-0 | Priority (0-7) |

## Usage in Code

### StreamState with LC

The `StreamState` dataclass now includes an optional `lc` field:

```python
@dataclass
class StreamState:
    # ... other fields ...
    lc: Optional[DMRLC] = None        # Link Control if extracted
    missed_header: bool = True         # Track if we missed the header
    embedded_lc_bits: bytearray = field(default_factory=bytearray)  # Accumulate embedded LC
    talker_alias: str = ""            # Talker alias if present
```

### Embedded LC Extraction (Smart Optimization)

HBlink4 intelligently extracts embedded LC **only when needed**:

```python
# In _handle_dmr_data():
if _frame_type in [1, 2]:  # Voice sync frame (header/terminator)
    _lc = extract_voice_lc(data)
    if _lc and _lc.is_valid:
        current_stream.lc = _lc
        current_stream.missed_header = False  # Got the header!

# Only extract embedded LC if we missed the header
elif _frame_type == 0 and current_stream.missed_header and current_stream.lc is None:
    # Voice frame - extract embedded LC fragments
    frame_within_superframe = _seq % 6  # 0-5, where 1-4 have embedded LC
    
    if 1 <= frame_within_superframe <= 4:
        embedded_fragment = extract_embedded_lc(data, frame_within_superframe)
        if embedded_fragment:
            current_stream.embedded_lc_bits.extend(embedded_fragment)
            
            # After 4 frames, decode the accumulated LC
            if len(current_stream.embedded_lc_bits) >= 8:
                embedded_lc = decode_embedded_lc(current_stream.embedded_lc_bits)
                if embedded_lc and embedded_lc.is_valid:
                    current_stream.lc = embedded_lc  # Recovered!
```

### DMRLC Dataclass

```python
@dataclass
class DMRLC:
    flco: int = 0            # Full Link Control Opcode
    fid: int = 0             # Feature ID
    service_options: int = 0 # Service options byte
    dst_id: int = 0          # Destination ID (24-bit)
    src_id: int = 0          # Source ID (24-bit)
    is_valid: bool = False   # Successfully decoded
    
    # Convenience properties
    @property
    def is_group_call(self) -> bool
    
    @property
    def is_private_call(self) -> bool
    
    @property
    def is_emergency(self) -> bool
    
    @property
    def privacy_enabled(self) -> bool
```

### Extracting LC

LC is automatically extracted from voice sync frames:

```python
# In _handle_dmr_data():
if _frame_type in [1, 2]:  # Voice sync or data sync
    _lc = extract_voice_lc(data)
    if _lc and _lc.is_valid:
        # LC information is available
        print(f"Source: {_lc.src_id}, Dest: {_lc.dst_id}")
        print(f"Group call: {_lc.is_group_call}")
        print(f"Emergency: {_lc.is_emergency}")
```

## Logging

When LC is extracted, HBlink4 logs the information:

### LC Extraction Debug Log
```
DEBUG - Extracted LC from sync frame: FLCO=0, src=312123, dst=9, group=True, emergency=False
```

### Stream LC Info Log
```
INFO - Stream LC info: repeater=312100 slot=1, src=312123, dst=9, call_type=GROUP, emergency=False, privacy=False
```

## Benefits

### Accurate Call Information

- LC provides authoritative source/destination IDs
- More reliable than packet headers in some scenarios
- Includes call type and service options

### Missing Header Recovery (Optimized)

- Embedded LC extracted only when header is missed (avoids processing overhead)
- Provides redundancy in packet loss scenarios
- Smart detection prevents unnecessary CPU cycles

### Emergency Detection

- Immediate detection of emergency calls
- Can trigger special handling or alerting

### Privacy Awareness

- Know when encryption is enabled
- Can avoid logging sensitive content

### Talker Alias Support ✅

- Human-readable callsign/name extraction
- Transmitted across multiple LC frames (header + 3 blocks)
- Supports 4 encoding formats:
  - **7-bit ASCII** (most common)
  - **ISO-8859-1** (Latin-1, for European characters)
  - **UTF-8** (Unicode)
  - **UTF-16BE** (Unicode, big-endian)
- Automatic collection and decoding
- Smart accumulation across frames

## Current Limitations

1. **Embedded LC Extraction**: Framework in place but bit-level extraction from AMBE frames not yet implemented
2. **No CRC Validation**: LC CRC is not currently checked (though data is already FEC-corrected by repeater)
3. **No LC Modification**: LC is read-only; FEC/CRC recalculation needed for stream forwarding (not yet implemented)

## Future Enhancements

### Complete Embedded LC Bit Extraction

Implement bit-level extraction from AMBE+2 vocoder frames:

```python
def extract_embedded_lc(data: bytes, frame_num: int) -> Optional[bytes]:
    """Extract 16 bits of embedded LC from voice frame B-E"""
    # TODO: Extract specific bits from AMBE+2 frame structure
    # Each frame B-E contains 16 bits of LC data at known bit positions
    # Requires understanding of AMBE+2 bit packing
```

### Talker Alias Optimization

Cache and reuse talker alias for the same source ID:

```python
class TalkerAliasCache:
    """Cache talker aliases by source ID"""
    def get(self, src_id: int) -> Optional[str]:
        # Return cached alias if available
        
    def set(self, src_id: int, alias: str):
        # Cache alias with TTL
```

### CRC and FEC for LC Modification

When forwarding/bridging streams with modified LC (future feature):

**Note**: Will use `dmr-utils3` library from PyPI for FEC calculations.

```python
# Using dmr-utils3 for FEC encoding
from dmrlink import fec

def encode_lc(lc: DMRLC) -> bytes:
    """Encode LC structure with FEC and CRC for forwarding"""
    # Pack LC fields into bytes
    lc_bytes = pack_lc_fields(lc)
    
    # Calculate and append CRC-16 (CCITT)
    crc = calculate_crc_ccitt(lc_bytes)
    lc_with_crc = lc_bytes + crc.to_bytes(2, 'big')
    
    # Apply FEC encoding using dmr-utils3
    fec_encoded = fec.encode_lc(lc_with_crc)
    
    return fec_encoded

def validate_lc_crc(lc_bytes: bytes, crc: int) -> bool:
    """Validate LC CRC-16 (for transmission error detection)"""
    calculated = calculate_crc_ccitt(lc_bytes)
    return calculated == crc
```

**Note**: FEC/CRC are only needed when **modifying** LC for forwarding. For read-only extraction, incoming data is already FEC-corrected by the source repeater.

### Talker Alias Decoding

Decode and display talker alias information:

```python
def decode_talker_alias(lc: DMRLC, blocks: List[DMRLC]) -> str:
    """Reconstruct talker alias from header and blocks"""
    # Combine header + 3 blocks
    # Return decoded alias string
```

## Testing

Comprehensive tests in `tests/test_lc_extraction.py` and `tests/test_talker_alias.py`:

- ✅ LC structure decoding
- ✅ Group call detection
- ✅ Private call detection
- ✅ Service options parsing
- ✅ Emergency/privacy flags
- ✅ Sync frame extraction
- ✅ Talker alias header detection
- ✅ Talker alias block extraction
- ✅ Talker alias decoding (all 4 formats)
- ✅ Multi-block collection and assembly
- 🔄 Embedded LC extraction (framework in place, bit extraction TODO)

Run tests:
```bash
python -m pytest tests/test_lc_extraction.py -v
python -m pytest tests/test_talker_alias.py -v
```

## References

- **ETSI TS 102 361-1**: DMR Air Interface Protocol
- **ETSI TS 102 361-2**: DMR Voice and Generic Services
- **DMR Association**: DMR Standard documentation
