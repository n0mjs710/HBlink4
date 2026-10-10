# DMR access checks and origin limits

## What HBlink4 checks

Inbound peers must match the configured repeater allowlist and ESSID range,
answer the HomeBrew `RPTK` challenge with the configured passphrase, follow the
handshake state sequence, and send later packets from the source IP and UDP
port bound to that session. DMR data is accepted only for a connected session;
timeslot and talkgroup policy controls routing.

`RPTK` uses a SHA-256 challenge construction, not a device certificate or a
per-device key. A captured exchange permits offline guesses if the shared
passphrase is weak. The protocol does not encrypt the session or attach a
per-packet MAC to inbound DMRD frames.

HBlink4 requires a fixed 302-byte `RPTC` configuration packet before it marks
a session connected. A malformed RPTC from the owning authenticated session is
rejected and removed. A malformed packet from another address cannot remove
the registered session. During an ID takeover, the incumbent remains active
until the authenticated claimant sends a valid RPTC and passes admission; a
rejected claimant cannot evict the incumbent.

## Strict Hotspot Access

BuenoDMR defaults `connection_type_detection.strict_hotspot_access` to enabled.
Only the literal JSON boolean `false` disables the default; missing or invalid
values fail closed. Strict mode runs in the HBP server after successful
allowlist/ESSID matching, `RPTK` authentication, expected handshake state, and
valid 302-byte RPTC. Only a session classified as `hotspot` enters `connected`;
DMRD is accepted only after that transition. With strict mode disabled, the
existing allowlist and authentication remain in force and classification is
still calculated for display.

The allowed profile is the existing deterministic `hotspot` category, computed
from client-provided RPTC metadata. Package ID is checked first; Software ID is
the fallback when Package ID does not match. Matching uses case-insensitive
substrings; network patterns take precedence over hotspot patterns, then
repeater patterns. An unmatched profile is `unknown` and is denied. This policy
means “admit clients that present an approved hotspot profile and pass the
configured checks,” not “prove a physical hotspot.” A compatible software
client with an allowed ID and passphrase can copy WPSD/MMDVM metadata, complete
the same HBP handshake, and send DMRD.

The Admin control requires an authenticated Admin role, CSRF validation, and
exact `ATIVAR` / `DESATIVAR` confirmation. The setting is persisted in the
HBlink4 config and SQLite metadata. Enabling is refused unless HBlink4 is
connected and every currently connected client classifies as `hotspot`.
Applying a change backs up the config and restarts HBlink4 using the existing
rollback flow. Audit actions are `hotspot_access_enabled`,
`hotspot_access_disabled`, and `client_admission_rejected`. Rejection records
include timestamp, repeater ID, validated callsign when available,
classification, and a fixed reason. They omit source IP, passphrase, auth hash,
and raw RPTC.

## What the protocol reveals

“Hotspot” is not a field in RPTL, RPTK, RPTO, or DMRD. It is a HBlink4-derived
category based on the client-provided Software ID and Package ID fields in
RPTC. RPTC is available after authentication and before the server transitions
the session to `connected`. Package ID is primary; Software ID is fallback.
The existing classifier is deterministic, case-insensitive substring matching.
Network patterns have priority over hotspot patterns, then repeater patterns.

| RPTC field | Offset | Length | Live values observed | Classification |
| --- | ---: | ---: | --- | --- |
| Software ID | 222 | 40 | `20260911_WPSD` | hotspot fallback |
| Package ID | 262 | 40 | `MMDVM_MMDVM_HS_Dual_Hat`, `MMDVM_MMDVM_HS`, `MMDVM_MMDVM_HS_Hat` | hotspot |

The four connected live sessions passively observed during validation were:

| Repeater ID | Callsign | Software ID | Package ID | Class | Strict result |
| ---: | --- | --- | --- | --- | --- |
| 724287002 | PY2DES | `20260911_WPSD` | `MMDVM_MMDVM_HS_Dual_Hat` | hotspot | allow |
| 724605415 | PY2SVS | `20260911_WPSD` | `MMDVM_MMDVM_HS` | hotspot | allow |
| 724287006 | PY2DES | `20260911_WPSD` | `MMDVM_MMDVM_HS_Hat` | hotspot | allow |
| 724015001 | PY2PMI | `20260911_WPSD` | `MMDVM_MMDVM_HS_Dual_Hat` | hotspot | allow |

This is a point-in-time observation of connected sessions, not proof that all
hardware or future client versions use these values.

MMDVM-Host sends the RPTL/RPTK/RPTC sequence and exposes the configuration
metadata described above. WPSD uses MMDVMHost as its core modem/RF layer. Neither
that software path nor HBP provides a cryptographic modem identity. See the
[MMDVM-Host DMR network implementation](https://github.com/g4klx/MMDVM-Host/blob/master/DMRNetwork.cpp),
[DMRGateway DMR network implementation](https://github.com/g4klx/DMRGateway/blob/master/DMRNetwork.cpp),
[WPSD overview](https://manual.wpsd.radio/get_started/),
[WPSD DMR guide](https://manual.wpsd.radio/modes/dmr/), and
[WPSD DMRGateway guide](https://manual.wpsd.radio/advanced/dmrgateway/).

## Signal limits

| Signal | What it establishes | Limitation |
| --- | --- | --- |
| Allowlisted repeater ID / ESSID | Claimed ID is in an admin-managed range | Packet fields can be copied |
| `RPTK` response | Sender knew the shared passphrase for the challenge | Credentials, not physical device identity |
| Handshake state | Peer followed the protocol order | Any compatible client can follow it |
| Source IP and UDP port | Packets match the authenticated session tuple | Session binding, not device identity; NAT and UDP spoofing remain relevant |
| RPTC software/package fields | Client-provided descriptive metadata | Self-reported and forgeable |
| `RPTO` / `RPTPING` | Requested talkgroups / session liveness | Do not identify hardware |
| DMRD IDs, slot, talkgroup and stream fields | Routing information used by HBlink4 | Not signed by a device-specific key |

There is no reliable HBP fingerprint for physical hotspot hardware. A software
client that knows an allowed ID and passphrase can implement the handshake,
declare WPSD/MMDVM metadata, bind its UDP session and send DMRD. An IP allowlist
could narrow access only for stable operator addresses; it would break roaming
and still would not prove a physical modem. No software-name or timing rule is
treated as attestation.

Strict mode protects the HBP DMR data plane and does not affect public dashboard
access from a phone, PC, or browser. Public dashboard APIs use a separate
sanitized data projection and do not expose the raw metadata, source addresses,
configuration, or authentication material.
