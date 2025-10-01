# DMR Talker Alias - Quick Reference

## What is Talker Alias?

A human-readable name/callsign transmitted in DMR that displays on receiving radios instead of just a numeric DMR ID.

## Transmission Structure

```
+---------------------------------------------+
| Talker Alias Transmission (4 LC Frames)    |
+---------------------------------------------+
|                                             |
|  Header (FLCO=4)                            |
|  +-------------------------------------+   |
|  | Format: 2 bits (0-3)                |   |
|  | Length: 6 bits (0-63 bytes)         |   |
|  | Data: 7 bytes                       |   |
|  +-------------------------------------+   |
|                                             |
|  Block 1 (FLCO=5)                           |
|  +-------------------------------------+   |
|  | Data: 7 bytes                       |   |
|  +-------------------------------------+   |
|                                             |
|  Block 2 (FLCO=6)                           |
|  +-------------------------------------+   |
|  | Data: 7 bytes                       |   |
|  +-------------------------------------+   |
|                                             |
|  Block 3 (FLCO=7)                           |
|  +-------------------------------------+   |
|  | Data: 7 bytes                       |   |
|  +-------------------------------------+   |
|                                             |
|  Total: 28 bytes maximum                    |
+---------------------------------------------+
```

## Encoding Formats

| Format | Name | Description | Example |
|--------|------|-------------|---------|
| 0 | 7-bit ASCII | Standard English text | "N0MJS" |
| 1 | ISO-8859-1 | Latin-1 (European chars) | "Café" |
| 2 | UTF-8 | Unicode, 1-4 bytes/char | "Test™" |
| 3 | UTF-16BE | Unicode, 2 bytes/char | "テスト" |

## Data Packing

Each LC frame packs 7 bytes of alias data into the LC fields:

```
+------------------------------------------+
| LC Structure (per frame)                 |
+------------------------------------------+
| dst_id (3 bytes)  -> Bytes 0-2 of alias |
| src_id (3 bytes)  -> Bytes 3-5 of alias |
| fid (1 byte)      -> Byte 6 of alias    |
+------------------------------------------+
```

## Example: "N0MJS Cort"

### Header (FLCO=4)
```
Format: 0 (7-bit ASCII)
Length: 11 bytes
Data: "N0MJS C" (7 bytes)
```

### Block 1 (FLCO=5)
```
Data: "ort\x00\x00\x00\x00" (7 bytes, padded)
```

### Blocks 2 & 3
Not needed (only 11 bytes total)

### Result
```
Decoded: "N0MJS Cort"
```

## HBlink4 Implementation

### Automatic Collection

```python
# StreamState tracks talker alias
current_stream.talker_alias          # "N0MJS Cort"
current_stream.talker_alias_format   # 0 (7-bit ASCII)
current_stream.talker_alias_length   # 11
current_stream.talker_alias_blocks   # {0: b'N0MJS C', 1: b'ort\x00...'}
```

### Processing Flow

```
Receive LC Frame
     |
     v
 Is FLCO=4?  --Yes--> Extract format & length
     |                Store block 0 data
     |                     |
     No                    v
     |              Have enough blocks?
     v                     |
 Is FLCO=5,6,7?  --Yes--> Store block N data
     |                     |
     No                    v
     |              Try to decode
     v                     |
  Ignore                   v
                  Success? --Yes--> Log and store alias
                           |
                          No
                           |
                           v
                    Wait for more blocks
```

## Logging Example

```
DEBUG - Talker alias header: format=0, length=11
DEBUG - Talker alias block 1 received
INFO - Talker alias: "N0MJS Cort" (format=0, blocks=[0, 1])
```

## Common Patterns

### Short Callsign
```
"W1ABC"
- Header only (5 bytes)
- No additional blocks needed
```

### Full Name + Callsign
```
"N0MJS Cort Buffington"
- Header: "N0MJS C"
- Block 1: "ort Buf"
- Block 2: "fingto"
- Block 3: "n\x00\x00\x00\x00\x00\x00"
Total: 22 bytes
```

### International
```
"JA1ABC 東京"
- Format: 2 (UTF-8)
- UTF-8 encoding for Japanese
- May need multiple blocks
```

## Benefits

[OK] **User-friendly**: Display names instead of numbers
[OK] **Multi-language**: Support for international characters
[OK] **Efficient**: Only 28 bytes maximum
[OK] **Automatic**: No manual configuration needed
[OK] **Real-time**: Updates during transmission

## Limitations

[X] **Length**: Maximum 28 bytes (varies by encoding)
[X] **Overhead**: Requires multiple LC frames
[X] **Not universal**: Not all radios support it
[X] **No verification**: No authentication of alias

## Testing

All formats tested and working:

```bash
python -m pytest tests/test_talker_alias.py -v

13 tests passing:
- Header detection
- Block extraction
- 7-bit ASCII decoding
- ISO-8859-1 decoding
- UTF-8 decoding
- UTF-16BE decoding
- Length trimming
- Padding removal
- Multi-block assembly
```

## See Also

- **docs/lc_extraction.md**: Complete LC extraction documentation
- **ETSI TS 102 361-2**: DMR standard specification
- **tests/test_talker_alias.py**: Full test suite
