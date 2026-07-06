# HBlink4 Release v4.8.0

Release date: 2026-05-06
Previous release: [v4.7.0](RELEASE_NOTES_v4.7.0.md) (2026-04-21)

## Overview

v4.8.0 closes the largest deferred item from v4.7.0 — **unit (private) call routing** — and lands a related correctness fix for DMR data calls that were previously being misrouted as voice. It also tightens the RPTO options parser so an explicit empty timeslot list is honored as deny-all instead of silently falling back to the configured defaults.

This is a feature release on the v4.7.x line; the HBP wire protocol and existing config formats are unchanged. New behavior is opt-in.

## Headline changes from v4.7.0

### 1. Unit (private) call routing

Subscriber-to-subscriber call routing, opt-in per repeater pattern and per outbound connection.

- **Source-side gating** — each repeater has a `unit_calls_enabled` flag, seeded from the pattern's new `default_unit_calls` (default `false`). Trusted repeaters can override per session via `UNIT=true|false` in their RPTO options string; untrusted repeaters stay on the pattern default with a warning if they try.
- **Target lookup** — cache hit fires a one-to-one to the target's repeater (or outbound link), cache miss broadcasts to every unit-enabled peer on the source's slot. Cache is the existing user cache, populated from observed traffic.
- **Subscriber-pair hang time** — protects an `(rf_src, dst_id)` pair on its slot. Either direction of the same pair passes; same source to a new target passes; anything else is a hijack and is contested like any other stream collision.
- **Cross-slot forwarding** — the originating slot is preserved on forward; a target's cached slot is informational only because DMR radios can monitor both slots simultaneously (slots are ships-in-the-night).
- **Outbound link propagation** — outbound connections add a per-link `unit_calls_enabled` flag. When `true`, local unit calls fan out over the link and unit calls arriving on the link are forwarded to local repeaters. Anti-loop: any unit call that *arrived* via an outbound is never forwarded to *any* outbound, which forms an implicit reverse-path tree across HBlink4 peers.
- **Normalized log shapes** — group and unit log lines now share an identical structure, differing only in `TS/TGID` vs `TS/RID` plus optional annotations (`[broadcast]`, `[one-to-one via N]`, etc.).

Known limitation: no cross-outbound forwarding for either group or unit calls in this release. A stream-id-based loop-detection design is sketched in [docs/TODO.md](TODO.md) for v4.9.x.

### 2. DMR data call classification

The HBP `call_type` bit only distinguishes group from unit *addressing* — not voice from data. Before v4.8.0, APRS beacons, SMS, and other DMR data calls landing on unit addressing were entering the unit-voice path and fanning out to every unit-enabled endpoint, with each burst churning through fast-terminator and contention logic.

- Streams are classified at first packet by `frame_type` / `dtype_vseq`. Data streams (frame_type=2 with dtype_vseq other than VHEAD/VTERM) follow a dedicated path: `StreamState.call_type="data"`, forwarding skipped, one log line per `(source, src, dst, slot)` inside a 2 s dedupe window so multi-burst beacons don't flood the log.
- Data Header frames (`dtype_vseq=6`) get a BPTC(196,96) decode to extract the data-call header for accurate classification and reporting. New decode helpers landed in [hblink4/lc.py](../hblink4/lc.py).
- `call_type` is split from payload kind: it stays `'group'` / `'private'` (addressing dimension), with a new `is_data` boolean alongside `is_assumed` (payload-kind dimension). Stream-start and stream-end events carry both.

Dashboard rendering picks up the split:
- **Gold DATA pill** on the slot status badge, gated by a 3 s client-side persistence-of-vision timer (a single data burst is too brief — ~60 ms on air + 200 ms fast_terminator — for the eye to catch otherwise).
- **Last Heard** rows now compose two emoji dimensions so both are visible: 👥/👤 for group/unit addressing, 🎤/📟 for voice/data payload.
- Last Heard's "Talkgroup" column is renamed to **"Destination"** and shows group/unit emoji indicators next to the value.

### 3. RPTO empty-timeslot fix

`Options=TS1=;TS2=3120` is the documented way to disable a slot in a repeater's RPTO subscription. Before v4.8.0, the receive-side parser silently dropped an empty `TS1=` value as "slot not specified," causing the resolver to fall back to the configured default TGs — so the repeater got the configured TS1 talkgroups even though it had explicitly asked for none.

The parser now tracks "slot mentioned" separately from "TGs requested." Empty `TS1=` / `TS2=` is honored as deny-all; a missing slot still falls back to config; `TS1=*` continues to mean "not specified, use defaults." This brings the receive-side parser into alignment with the outbound-side parser at [hblink4/hblink.py](../hblink4/hblink.py) `_parse_options`.

The regression test in [tests/test_routing_optimization.py](../tests/test_routing_optimization.py) was rewritten to drive the real `_handle_options` end-to-end (the previous version was a parser re-implementation that couldn't catch this class of bug).

## Bug fixes

- RPTO empty-TS deny-all (covered above as a headline item).
- Data-call event addressing dimension preserved — initial data-classification cut flattened the addressing bit into a single `'data'` value, which made the dashboard render every data call as 👥📟 (group) regardless of whether it was actually unit-addressed. Split now preserves both dimensions.

## Migration notes

### From v4.7.0 to v4.8.0

- **No config format break.** Existing configurations keep working.
- **Unit calls are opt-in.** Add `default_unit_calls: true` to a repeater pattern to enable participation; trusted repeaters can also opt in/out via `UNIT=` in their RPTO. Repeaters without this flag stay on group-only behavior.
- **Outbound unit propagation is opt-in.** Set `unit_calls_enabled: true` on an outbound connection only if the remote peer is also routing unit calls (e.g. another HBlink4 server).
- **RPTO empty-slot fix changes behavior** for any repeater currently sending `Options=TS1=;TS2=...` (or vice versa). Those repeaters were *unintentionally* getting the configured default TGs on the empty slot; after the upgrade they will correctly get an empty set (no traffic on that slot), which is what they were asking for. If you have repeaters relying on the old behavior, switch them to either omitting the slot entirely (`Options=TS2=...`) or listing the desired TGs explicitly.
- **Dashboard** should be restarted along with the server to pick up the new event fields and the DATA pill / dual-emoji rendering.

### Rollback

```bash
cd /home/cort/hblink4
git checkout v4.7.0
systemctl restart hblink4
systemctl restart hblink4-dash
```

Rolling back loses unit calls and the data-call classification, and re-introduces the RPTO empty-slot bug. The HBP wire protocol is identical so connected repeaters don't need to be reconfigured.

## Version tags

- **Previous**: v4.7.0 (2026-04-21)
- **Current**: v4.8.0 (this release)
- **Next**: v4.9.x — cross-outbound forwarding (group + unit) is the largest deferred item; see [TODO.md](TODO.md)

## Related documentation

- [docs/configuration.md](configuration.md) — `default_unit_calls`, outbound `unit_calls_enabled`, `UNIT=` RPTO override
- [docs/stream_tracking.md](stream_tracking.md) — unit-call lifecycle, subscriber-pair hang time, data-call classification path
- [docs/TODO.md](TODO.md) — remaining roadmap (cross-outbound forwarding, performance monitoring, config UI)
- [docs/dmrd_translation.md](dmrd_translation.md) — RPTO extended grammar (unchanged from v4.7.0)
- [docs/connecting_to_hblink4.md](connecting_to_hblink4.md) — operator-facing `Options=` examples
