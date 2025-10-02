# Using dmr_utils3 in HBlink4

## Why Use dmr_utils3?

HBlink4 now uses the `dmr_utils3` library for all DMR protocol decoding tasks. This is a strategic decision to leverage mature, community-tested code rather than reimplementing complex algorithms.

## Benefits

### 1. **Proven Reliability**
- Used in production HBlink3 systems worldwide since 2016
- Thousands of hours of real-world testing
- Community bug reports and fixes
- Known to work correctly with all major DMR manufacturers

### 2. **Proper FEC Implementation**
- **Reed-Solomon RS(12,9):** Used for LC headers and terminators
- **Hamming (16,11,4):** Used for embedded LC
- **BPTC(196,96):** Block Product Turbo Codes for full LC encoding
- All FEC algorithms properly implemented with error correction

### 3. **Complex Encoding Handled**
- BPTC interleaving and deinterleaving
- Proper bit packing and unpacking
- Sync pattern recognition
- Checksum validation (CRC-5, CRC-9, CRC-CCIT)

### 4. **Eliminates Custom Errors**
- No need to reimplement complex bit manipulation
- No byte ordering confusion
- No off-by-one errors in bit extraction
- Consistent with ETSI specifications

### 5. **Maintainability**
- Community maintains the library
- Bug fixes benefit all users
- Security updates automatically available
- Well-documented code

### 6. **Interoperability**
- Same decoding as HBlink3
- Consistent with other DMR tools (dmrlink, FreeDMR)
- Follows ETSI standards exactly

## What dmr_utils3 Provides

### decode Module
```python
from dmr_utils3 import decode

# Voice header/terminator with BPTC decoding
decoded = decode.voice_head_term(dmr_payload)
# Returns: {'LC': bytes, 'CC': bytes, 'DTYPE': bytes, 'SYNC': bitarray}

# Voice burst with embedded LC extraction  
decoded = decode.voice(dmr_payload)
# Returns: {'AMBE': [ambe0, ambe1, ambe2], 'CC': bytes, 'LCSS': bytes, 'EMBED': bitarray}
```

### bptc Module
```python
from dmr_utils3 import bptc

# Decode embedded LC with Hamming FEC
lc_bytes = bptc.decode_emblc(bits_128)
# Returns: 9 bytes [Options:3][Dst:3][Src:3]

# Decode full LC from voice header/term
lc_bytes = bptc.decode_full_lc(bits_196)
# Returns: 9 bytes [Options:3][Dst:3][Src:3]

# Encode LC for transmission
encoded = bptc.encode_header_lc(lc_bytes_9)
# Returns: 196 bits BPTC-encoded, interleaved, ready to transmit
```

### Other Modules
- **golay**: Golay(23,12) and Golay(20,8) FEC
- **hamming**: Hamming(16,11,4), Hamming(15,11,3), Hamming(13,9,3)
- **qr**: Quadratic Residue(16,7,6) codes
- **rs129**: Reed-Solomon(12,9) for LC
- **crc**: CRC-5, CRC-9, CRC-CCIT checksums
- **utils**: DMR ID utilities, bytes conversion helpers

## HBlink4 Integration

### Before (Custom Implementation)
```python
def decode_lc(lc_bytes: bytes) -> DMRLC:
    # Complex bit manipulation prone to errors
    lc.dst_id = ((lc_bytes[2] & 0x03) << 22) | (lc_bytes[3] << 14) | ...
    lc.src_id = ((lc_bytes[5] & 0x03) << 22) | (lc_bytes[6] << 14) | ...
    # What if byte ordering is wrong?
    # What about FEC?
    # How to validate?
```

### After (Using dmr_utils3)
```python
from dmr_utils3 import decode, bptc

def extract_voice_lc(data: bytes) -> Optional[DMRLC]:
    dmr_payload = data[20:53]
    # Let dmr_utils3 handle all the complexity
    decoded = decode.voice_head_term(dmr_payload)
    if decoded and 'LC' in decoded:
        # Simple byte extraction - dmr_utils3 already decoded it
        lc.dst_id = int.from_bytes(decoded['LC'][3:6], 'big')
        lc.src_id = int.from_bytes(decoded['LC'][6:9], 'big')
    return lc
```

## Installation

```bash
pip install dmr_utils3
```

**Requirements:**
- `bitstring>=3.1.5` (automatically installed)
- `bitarray>=0.8.3` (automatically installed)

## Documentation

- **GitHub:** https://github.com/n0mjs710/dmr_utils3
- **PyPI:** https://pypi.org/project/dmr-utils3/
- **Author:** Cortney T. Buffington, N0MJS (same as HBlink!)

## License

dmr_utils3 is licensed under GNU GPLv3, compatible with HBlink4's license.

## When to Use dmr_utils3

✅ **Always use for:**
- LC extraction from voice headers/terminators
- Embedded LC decoding
- Any FEC encoding/decoding
- Checksum calculations
- BPTC operations

❌ **Don't need for:**
- Basic Homebrew packet parsing (header fields)
- Simple byte extraction
- Network operations
- State management

## Migration Notes

If you see errors like:
- "src and dst appear swapped"
- "IDs don't match expected values"
- "Checksum validation fails"

**Check:** Are you using dmr_utils3 output correctly?
- dmr_utils3 returns already-decoded, byte-aligned data
- No need for bit manipulation on the output
- Structure is always: `[Options:3][Dst:3][Src:3]`

## Performance

dmr_utils3 is optimized C-accelerated Python:
- Fast enough for real-time decoding
- Minimal CPU overhead
- Used in production systems handling thousands of simultaneous calls

## Support

Issues with dmr_utils3 should be reported to:
- https://github.com/n0mjs710/dmr_utils3/issues

The library is actively maintained by the author of HBlink.
