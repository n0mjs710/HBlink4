# HBlink4 Release v4.7.0 — Production Release

Release date: 2026-04-21
Previous release: [v4.6.1](RELEASE_NOTES_v4.6.1.md) (2025-11-01, Twisted-based, no DMRA)

## Overview

v4.7.0 is the first production release after the Twisted → asyncio migration. It replaces the entire network stack, adds Talker Alias (DMRA) support, introduces server-to-server outbound connections, and ships the per-repeater DMRD translation subsystem with on-the-wire Link Control rewriting. Internally, the codebase has been split from a single module into a package of focused modules (`config`, `protocol`, `utils`, `models`, `lc`).

This is intended to be the new mainline baseline.

## Headline changes from v4.6.1

### 1. Twisted → asyncio migration (complete)

v4.6.1 explicitly noted it was the "last Twisted-based release." v4.7.0 completes the migration that was on `feature/asyncio-migration` at that point:

- Pure `asyncio.DatagramProtocol` UDP I/O with no external framework dependency
- Twisted, `pyOpenSSL`, and `service_identity` removed from [requirements.txt](../requirements.txt)
- DatagramProtocol compatibility fixes for standard asyncio semantics
- Graceful shutdown paths for both server and dashboard (proper WebSocket close under systemd)

No operator-facing configuration change is required to migrate from v4.6.1 — the HBP protocol on the wire is identical.

### 2. Talker Alias (DMRA) support

- `DMRA` packet type is now parsed and forwarded alongside `DMRD`
- Talker alias text is logged at DEBUG level (moved from the noisier default to reduce log spam)
- v4.6.1's release note listed DMRA as a known gap; v4.7.0 closes it

### 3. Network Outbound connections

A brand-new feature: HBlink4 can now act as a repeater *to* another DMR server, in addition to accepting inbound repeaters. This is the "server-to-server link" use case.

- Configured via the new `outbound_connections` block — see [config_advanced_sample.json](../config/config_advanced_sample.json) (this block lived in `config_sample.json` at the time of this release; the samples were split later)
- Full protocol state machine (RPTL → RPTK → RPTC → RPTO → keepalive) implemented outbound
- Per-connection TDMA slot tracking so we don't transmit on a slot that's already busy with an incoming RX stream
- Graceful shutdown sends `RPTCL` to each remote server
- ID-conflict protection prevents two outbound connections from claiming the same radio ID
- Full dashboard integration (connection state, stream events, last-heard)

### 4. DMRD translation (per-repeater)

Trusted repeaters can now declare slot / TGID remap rules and an outbound `rf_src` override via the RPTO options string. This lets a site use its own local talkgroup numbering / slot layout while the rest of the network keeps one consistent vocabulary.

- Extended RPTO grammar: `TS1=net_tgid[:local_slot[:local_tgid]]`, with inclusive ranges (`N-M`) and wildcards (`*`) in the local-side fields
- Outbound `SRC=radio_id` rewrites the rf_src on every group-voice packet leaving the repeater (one-way, group-only, trust-gated)
- Trust flag required; untrusted repeaters get translation syntax rejected with a warning
- Most-specific rule wins on collision (exact=3 > range=2)
- Network-side addressing is rejected for any TG that has a translation declared (prevents the vocabulary from being bypassed)
- Zero-cost fast path when no translation is declared

Full reference: [docs/dmrd_translation.md](dmrd_translation.md).

### 5. Link Control rewriting on translated frames

The initial translation cut rewrote DMRD headers but left the 33-byte DMR payload untouched (or zero-blanked on data-sync frames). This caused MMDVMHost and subscriber radios to reassemble embedded LC fragments from voice bursts B-E and eventually see addressing that contradicted the header — the symptom was a "works for 1-2s, then corrupts across all receiving repeaters" failure mode in early testing.

v4.7.0 now re-encodes the embedded Link Control so the payload matches the rewritten header:

- **VHEAD** (voice header data-sync frame) — full 196-bit BPTC LC spliced into payload bits `[0:98]` + `[166:264]`, 68-bit slot-type/sync window preserved
- **VTERM** (voice terminator data-sync frame) — same splice pattern with the terminator LC codeword
- **Voice bursts B/C/D/E** — 32-bit EMB_LC fragment spliced into bits `[116:148]`; AMBE vocoder bits are bit-identical

All FEC/interleave math is delegated to `dmr_utils3.bptc` (a new explicit dependency). Per-stream cache means one BPTC encode per unique outbound `(dst, src)` tuple. Untranslated forwards stay on the zero-copy fast path.

MMDVMHost's "LC fall back" warning lines are gone as a side effect.

### 6. Automated radioid.net user database refresh (dashboard)

The dashboard previously relied on a manual `wget + filter_user_csv.py` pipeline to keep callsign lookups fresh. v4.7.0 adds a daily in-process refresh:

- Conditional HTTPS GET against `database.radioid.net/static/user.csv` with `If-Modified-Since`, gzip-aware
- Configurable country / callsign-regex / radio-ID filters (defaults match the existing US+Canada manual filter byte-for-byte)
- JSON snapshot + sidecar metadata on disk; hot-swap of the in-memory `radio_id → callsign` dict with no dashboard restart
- Three-stage pipeline (download → filter → store); failure in any stage is non-fatal and the last known-good snapshot keeps serving
- No new external dependencies (stdlib `urllib.request` only)
- `scripts/filter_user_csv.py` remained as a manual fallback (removed in a later release)

### 7. Code organization refactor

The single `hblink.py` module has been split into a proper package:

- [hblink4/config.py](../hblink4/config.py) — configuration loading / parsing / validation
- [hblink4/protocol.py](../hblink4/protocol.py) — DMRD packet parsing, terminator detection, command dispatch
- [hblink4/utils.py](../hblink4/utils.py) — shared helpers (ID conversion, connection-type detection, log formatting)
- [hblink4/models.py](../hblink4/models.py) — dataclasses (`StreamState`, `RepeaterState`, `OutboundState`, `OutboundConnectionConfig`)
- [hblink4/lc.py](../hblink4/lc.py) — DMR Link Control encode/splice helpers (new in v4.7.0)

Plus a terminology standardization pass ("system" / "peer" / legacy terms → "repeater" / "outbound connection" / network-aware names).

### 8. Hot-path performance

- Set-based talkgroup storage (O(1) membership test, no per-packet bytes-int conversion)
- Per-stream routing cache — targets calculated once at stream start, reused for every packet in that stream
- Eliminated unbounded caches that were leaking memory under sustained load
- Dashboard API: eliminated redundant byte conversions on event emission

### 9. Stream handling

- Unified `_end_stream()` helper consolidates 4 previously-divergent stream ending code paths
- Immediate terminator detection (~60ms) via HBP flags, 2s timeout fallback for lost terminators
- Hang time protects TG conversations without blocking fast TG switching by the same user
- RX streams always win over assumed (TX) streams on the same slot
- Translation-aware stream-start and stream-end log messages show `TS/TGID: net_slot/net_tgid (rf: rf_slot/rf_tgid)` when addressing differs

### 10. Dashboard improvements

- Last Heard source display (shows whether a user was heard directly or via an outbound connection)
- Outbound connection RX streams now feed the Last Heard list
- TX/RX direction fix (was showing TX streams as RX in some contexts)
- TG display sends hex strings to avoid encoding ambiguity

## Bug fixes

- Config validation now uses `radio_id` (was inconsistent with older `our_id`)
- Full RPTC field set now populated correctly
- Hang time clarified to protect TG conversations specifically
- DNS resolution uses `socket.SOCK_DGRAM` (was incorrectly `asyncio.SOCK_DGRAM`)
- Stream terminator detection reliability
- WebSocket shutdown robustness under systemd
- Outbound slot card TX/RX visualization
- Inbound reject message now prints when a repeater with translation rules keys the net-side address for a translated TG (was silently passing through)

## Migration notes

### From v4.6.1 to v4.7.0

- **No config format break** for existing repeater configurations. Existing `slot1_talkgroups` / `slot2_talkgroups` lists keep working unchanged.
- **New optional config blocks** become available: `outbound_connections` for server-to-server links, and the `dashboard.user_database_refresh` block to enable automated radioid.net pulls.
- **Translation is opt-in** — a repeater must be marked `trust: true` and declare translation via extended RPTO syntax. Repeaters that don't see no change.
- **Dashboard** should be restarted along with the server to pick up new event types.

### Rollback

If issues arise and you need to roll back to v4.6.1 (Twisted-based):

```bash
cd /home/cort/hblink4
git checkout v1.6.1      # the old tag — v4.6.1 kept its original v1.6.1 tag
systemctl restart hblink4
systemctl restart hblink4-dash
```

Note: rolling back loses DMRA, outbound connections, and translation. The HBP wire protocol is identical so connected repeaters don't need to be reconfigured.

## Version tags

- **Previous**: v4.6.1 (2025-11-01, Twisted-based, no DMRA)
- **Current**: v4.7.0 (this release)
- **Next**: v4.8.x — unit (private) call routing is the largest deferred item (see [TODO.md](TODO.md))

## Related documentation

- [docs/configuration.md](configuration.md) — complete configuration reference (includes outbound connections, dashboard user DB refresh, trust flag)
- [docs/routing.md](routing.md) — per-repeater routing, ACLs, contention, assumed slot state
- [docs/dmrd_translation.md](dmrd_translation.md) — RPTO extended grammar, translation semantics, LC rewriting, use cases
- [docs/connecting_to_hblink4.md](connecting_to_hblink4.md) — operator-facing `Options=` string examples
- [docs/protocol.md](protocol.md) — HBP packet layout reference
- [docs/stream_tracking.md](stream_tracking.md) — stream lifecycle, contention, assumed vs real streams
- [docs/OPENBRIDGE_ANALYSIS.md](OPENBRIDGE_ANALYSIS.md) — reference analysis for future OpenBridge support (not implemented in v4.7.0)

## Credits

Work on this release spanned several feature branches including `feature/asyncio-migration`, the outbound-connection work, and `dmrd-translation`. Commits since the v1.6.1 tag: 63.
