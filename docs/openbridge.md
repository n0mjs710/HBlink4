# OpenBridge (OBP) Trunks

OpenBridge is a connectionless, HMAC-authenticated sub-protocol of the HomeBrew
DMR family designed for **server-to-server** traffic. Where the standard
HomeBrew Protocol (HBP) emulates a repeater with a full login/keepalive
handshake and two timeslots, OpenBridge is a **stream-multiplexed trunk**: no
handshake, per-packet HMAC authentication, and many concurrent call streams
rather than a two-slot air interface.

HBlink4 uses OpenBridge to **trunk many talkgroups' worth of traffic** to and
from an upstream core (typically HBlink3), while HBlink4 continues to be the
device-facing edge — accepting repeaters/hotspots and making its normal
per-device delivery decisions. This keeps the two roles cleanly separated: the
core routes (operator-controlled), the edge delivers (device/options-controlled),
and OpenBridge is the trunk between them.

> Background/feasibility analysis: [OPENBRIDGE_ANALYSIS.md](OPENBRIDGE_ANALYSIS.md).

---

## Design principles

These are deliberate and enforced; understanding them explains the config.

- **The wire TGID is the canonical TGID (no OBP-edge translation).** An OBP edge
  assigns a local **timeslot** to each incoming TGID but does **not** renumber
  it. Backbone TGIDs carry agreed meaning; renumbering belongs only where there
  is governance for it — a device edge (governed by the repeater operator via
  `options`) or an HBlink3 bridge (governed by rules). The OBP backbone boundary
  preserves the number.
- **Stream-multiplexed, not two-slot.** OBP concurrency is not gated by
  timeslots. Each OBP tracks many concurrent streams by `stream_id`; the wire
  slot is a convention (always TS1) and carries no meaning.
- **One OBP per canonical TGID.** A given canonical TGID may be carried by at
  most one **enabled** OBP. This makes OBP→OBP transit structurally impossible
  (HBlink4 is an edge, not a transit router) and prevents duplication. Enforced
  at startup (hard error).
- **Fail-closed.** Traffic for a TGID not in an OBP's `talkgroup_slots` map is
  dropped, in both directions.
- **Reflection guard.** A stream is never sent back out the OBP it arrived on.
  (Multi-node "long-path" loops across a federated mesh remain an operator
  coordination concern, as they are for all amateur DMR.)

---

## How it fits the routing engine

An OBP is just another **source** and **sink** in HBlink4's calculate-once /
cache-per-stream routing core:

- **Ingress** (OBP → edge devices): a frame is authenticated (HMAC + source
  socket), filtered (drop unmapped TGID), and assigned its local timeslot from
  `talkgroup_slots`; the wire slot bit is normalized to that TS so the frame is
  coherent in HBlink4's `(TS, TGID)`-keyed core. Targets (repeaters, outbounds)
  are computed once at stream start and cached.
- **Egress** (edge devices → OBP): an OBP that owns a stream's canonical TGID is
  an eligible target. The frame is sent with canonical addressing (no remap),
  the RptrId set per `preserve_source_peer`, wire slot forced to TS1, and a fresh
  HMAC appended.

Because target selection is per-stream (not per-packet), OBP adds no per-packet
routing cost. See [Call Routing](routing.md).

---

## Configuration

OpenBridge trunks are declared in a top-level `openbridge_connections` array in
the main HBlink4 config (JSON). See also
[Configuration Guide → OpenBridge Trunks](configuration.md#openbridge-trunks).

```json
"openbridge_connections": [
    {
        "enabled": true,
        "name": "KS-Core-Trunk",
        "network_id": 3129900,
        "local_address": "0.0.0.0",
        "local_port": 62035,
        "target_address": "core.example.net",
        "target_port": 62035,
        "passphrase": "shared-obp-secret",
        "preserve_source_peer": true,
        "talkgroup_slots": {
            "31": "1",
            "8": "1",
            "3120": "2",
            "3100": "2"
        }
    }
]
```

### Fields

| Field | Meaning |
|-------|---------|
| `enabled` | Start this trunk. Disabled trunks are skipped (see failover below). |
| `name` | Unique trunk name (shown in logs and on the dashboard). |
| `network_id` | This side's OBP network ID. Stamped into the DMRD RptrId on egress **only** when `preserve_source_peer` is `false`. |
| `local_address` / `local_port` | Local bind for this trunk's own UDP socket (one socket per OBP). |
| `target_address` / `target_port` | The remote OBP peer. Ingress is accepted only from this socket. |
| `passphrase` | Shared secret; keys the per-packet HMAC-SHA1. Must match the peer. |
| `preserve_source_peer` | `true` (default): keep the true source peer/repeater ID in the egress RptrId (transparency; lets a downstream dashboard show the real origin). `false`: overwrite with `network_id` (Brandmeister-spec behavior). Set `false` for strict-spec peers. |
| `talkgroup_slots` | Canonical **TGID → local TS** map. Does triple duty: ownership, fail-closed filter, and timeslot assignment. |

### `talkgroup_slots` — why both sides are quoted

JSON object keys must be strings, so the TGID key is quoted; the TS value is
quoted too so the file reads consistently (`"31": "1"`, not `"31": 1`). Both are
parsed to their real types at load (3-byte TGID, integer TS). The map form
structurally guarantees **one TS per TGID** (a key can't hold two slots).

### Validation (fatal at startup)

- TS must be `1` or `2`; TGID must be in range.
- **One OBP per canonical TGID** across all *enabled* trunks — a TGID claimed by
  two enabled OBPs is a hard error (would make HBlink4 a transit processor).

### Manual failover

Because only *enabled* trunks are checked for TGID ownership, a **disabled**
standby trunk may mirror the active trunk's `talkgroup_slots`. Flip `enabled` to
switch over. (Two *active* trunks carrying the same TGID would double audio, so
that is rejected — real failover is one live owner at a time.)

---

## Dashboard

Each trunk appears in an **OpenBridge** section: name, network ID, peer, uptime,
whether it preserves the source peer, its TGID→TS map, and a live list of the
streams currently crossing it. OBP calls also count toward the daily stats and
appear in **Last Heard** like any received traffic. For deep diagnostics, watch
the `[OBP <name>] RX stream start/end …` log lines.

---

## Reference topology (HBlink3 core + HBlink4 edge)

```
 repeaters / hotspots            OpenBridge trunk              upstream talkgroups
        │                    (many TGIDs, one socket)                  │
        ▼                                                              ▼
 ┌───────────────┐   OBP: canonical TGID + per-packet HMAC   ┌──────────────────┐
 │   HBlink4     │◀────────────────────────────────────────▶│     HBlink3      │
 │ (edge master) │   TGID is preserved; TS assigned locally  │  (core router)   │
 └───────────────┘                                            └──────────────────┘
   delivers per device,                                        routes per operator
   options-controlled                                          rules (bridges)
```

HBlink4 ingests the trunk's TGIDs, assigns each a local timeslot, and delivers to
its devices per their subscriptions; device transmissions egress back up the
trunk under their canonical TGID. Numbering collisions between two upstream
networks are resolved upstream (or at a governed HBlink3 core), never at
HBlink4's edge.
