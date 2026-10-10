"""
User database lifecycle for the dashboard.

Owns the radio_id -> callsign/profile mapping: loads a JSON snapshot at startup,
refreshes from radioid.net's static CSV dump on a daily schedule, and hot-swaps
the in-memory dict so stream_start callsign lookups pick up new data without a
dashboard restart.

The pipeline is three independent stages:

    download -> filter -> store

Failure in any stage is non-fatal; the last known-good snapshot keeps serving.

Data contract:
    - Snapshot file: JSON object mapping stringified radio_id to callsign or
      [callsign, public name, city, region]. Legacy callsign-only files load.
    - Sidecar metadata: {last_modified_header, row_count, refresh_timestamp,
      source_url, source_status}
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import io
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Safety caps
_MAX_RESPONSE_BYTES = 100 * 1024 * 1024  # 100 MB — upstream is ~25 MB today
_HTTP_CONNECT_TIMEOUT = 30.0
_HTTP_TOTAL_TIMEOUT = 120.0

# Placeholder UA string from the proposal — we warn if the operator didn't change it.
_PLACEHOLDER_CONTACT = "operator@example.org"


@dataclass
class UserDbMeta:
    """Sidecar metadata persisted alongside the snapshot."""
    source_url: str = ""
    last_modified_header: str = ""     # From upstream; used as If-Modified-Since
    refresh_timestamp: float = 0.0     # When we last successfully wrote the snapshot
    row_count: int = 0
    source_status: str = "unknown"     # "ok", "not_modified", "error", "unknown"
    last_error: str = ""               # Human-readable error from last failed attempt
    profile_schema: int = 0             # 1 once the snapshot includes public directory fields


class UserDatabase:
    """
    In-memory radio_id -> callsign mapping with background refresh.

    Concurrency model: the dashboard is a single-process asyncio app. The read
    path is a single dict .get(); the write path swaps the dict reference
    wholesale after a successful filter. Python assignment is atomic under the
    GIL, so readers see either the old dict or the new dict — never a partial
    state. No lock needed.
    """

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._snapshot_path = self._data_dir / "user_db.json"
        self._meta_path = self._data_dir / "user_db.meta.json"
        self._data: Dict[int, Union[str, tuple]] = {}
        self._meta = UserDbMeta()
        self._lock = asyncio.Lock()  # Serializes refresh attempts

    # ---------------------------------------------------------------- reads

    def get(self, radio_id: int, default: str = "") -> str:
        entry = self._data.get(radio_id)
        if isinstance(entry, str):
            return entry
        return entry[0] if isinstance(entry, (list, tuple)) and entry else default

    def get_public_profile(self, radio_id: int) -> dict:
        """Optional directory fields; no address or contact fields enter the cache."""
        entry = self._data.get(radio_id)
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            return {}
        return {key: value for key, value in zip(("name", "city", "region"), entry[1:]) if value}

    def __len__(self) -> int:
        return len(self._data)

    @property
    def meta(self) -> UserDbMeta:
        return self._meta

    def status_dict(self) -> dict:
        """Serializable meta for WebSocket / API exposure."""
        d = asdict(self._meta)
        d["loaded_rows"] = len(self._data)
        return d

    # -------------------------------------------------- startup / disk load

    def load_from_disk(self) -> None:
        """
        Load snapshot + meta from disk. Safe to call on startup even when the
        files don't exist yet. Errors log a warning and leave the in-memory
        dict empty — the first successful refresh will populate it.
        """
        if self._meta_path.exists():
            try:
                with open(self._meta_path) as f:
                    raw = json.load(f)
                    self._meta = UserDbMeta(**{
                        k: v for k, v in raw.items() if k in UserDbMeta.__annotations__
                    })
            except Exception as e:
                logger.warning(f"⚠️ Could not parse user_db meta file: {e}")

        if self._snapshot_path.exists():
            try:
                with open(self._snapshot_path) as f:
                    raw = json.load(f)
                # JSON keys are strings — re-key to int for hot-path lookup.
                self._data = {
                    int(k): tuple(v) if isinstance(v, list) and len(v) == 4 else v
                    for k, v in raw.items()
                    if isinstance(v, str) or (isinstance(v, list) and len(v) == 4
                                              and all(isinstance(item, str) for item in v))
                }
                logger.info(
                    f"📘 Loaded user database snapshot: {len(self._data):,} entries "
                    f"(refreshed {_age_str(self._meta.refresh_timestamp)} ago)"
                )
            except Exception as e:
                logger.warning(f"⚠️ Could not load user_db snapshot: {e}")
        else:
            logger.info(
                "📘 No user_db snapshot found — callsign lookups return empty until first refresh"
            )

    def snapshot_age_hours(self) -> Optional[float]:
        """None if no snapshot; else hours since last successful refresh."""
        if not self._meta.refresh_timestamp:
            return None
        return (time.time() - self._meta.refresh_timestamp) / 3600.0

    # --------------------------------------------------------- refresh path

    async def refresh_from_upstream(self, config: dict) -> str:
        """
        Run one download -> filter -> store cycle. Returns a status string:
        "ok", "not_modified", "disabled", "error".

        Config shape (dashboard config["user_database"]):
            source_url, user_agent, filter {...}, fallback {...}

        All blocking work runs via asyncio.to_thread so the event loop stays
        responsive even if radioid.net is slow.
        """
        if not config.get("enabled", True):
            logger.debug("User database refresh disabled in config")
            return "disabled"

        if self._lock.locked():
            logger.info("⏳ User database refresh already in progress, skipping this trigger")
            return "busy"

        async with self._lock:
            return await asyncio.to_thread(self._refresh_sync, config)

    def _refresh_sync(self, config: dict) -> str:
        """Blocking refresh pipeline — runs off the event loop."""
        source_url = config.get("source_url", "https://database.radioid.net/static/user.csv")
        user_agent = config.get("user_agent", "HBlink4-Dashboard (contact=operator@example.org)")
        filter_cfg = config.get("filter", {}) or {}
        fallback_cfg = config.get("fallback", {}) or {}

        self._meta.source_url = source_url

        t_start = time.time()

        # ------ Stage 1: download
        try:
            status, body, last_modified = _http_get_with_conditional(
                source_url,
                user_agent=user_agent,
                if_modified_since=(self._meta.last_modified_header or None)
                if self._meta.profile_schema >= 1 else None,
            )
        except Exception as e:
            msg = f"download failed: {e}"
            logger.warning(f"⚠️ user_db refresh: {msg}")
            self._meta.source_status = "error"
            self._meta.last_error = msg
            self._write_meta_best_effort()
            return "error"

        if status == 304:
            # Upstream unchanged — just touch the timestamp so the
            # "too stale" startup check doesn't re-fire on every reboot.
            self._meta.refresh_timestamp = time.time()
            self._meta.source_status = "not_modified"
            self._meta.last_error = ""
            self._write_meta_best_effort()
            logger.info(
                f"📘 user_db unchanged upstream (304 Not Modified, {time.time() - t_start:.1f}s)"
            )
            return "not_modified"

        if status != 200:
            msg = f"HTTP {status}"
            logger.warning(f"⚠️ user_db refresh: {msg} (UA was {user_agent!r})")
            self._meta.source_status = "error"
            self._meta.last_error = msg
            self._write_meta_best_effort()
            return "error"

        # ------ Stage 2: filter
        try:
            new_data = filter_profile_rows_from_csv_bytes(body, filter_cfg)
        except Exception as e:
            msg = f"filter failed: {e}"
            logger.warning(f"⚠️ user_db refresh: {msg}")
            self._meta.source_status = "error"
            self._meta.last_error = msg
            self._write_meta_best_effort()
            return "error"

        min_rows = int(fallback_cfg.get("min_rows_required", 1000))
        if len(new_data) < min_rows:
            msg = (
                f"filter produced {len(new_data)} rows, below min_rows_required={min_rows} "
                f"(keeping old snapshot of {len(self._data)} rows)"
            )
            logger.warning(f"⚠️ user_db refresh: {msg}")
            self._meta.source_status = "error"
            self._meta.last_error = msg
            self._write_meta_best_effort()
            return "error"

        # ------ Stage 3: store + swap
        try:
            self._write_snapshot_atomic(new_data)
        except Exception as e:
            msg = f"write failed: {e}"
            logger.warning(f"⚠️ user_db refresh: {msg}")
            self._meta.source_status = "error"
            self._meta.last_error = msg
            self._write_meta_best_effort()
            return "error"

        # Swap in-memory reference. Single assignment under the GIL — atomic.
        old_rows = len(self._data)
        self._data = new_data
        self._meta.row_count = len(new_data)
        self._meta.profile_schema = 1
        self._meta.last_modified_header = last_modified or ""
        self._meta.refresh_timestamp = time.time()
        self._meta.source_status = "ok"
        self._meta.last_error = ""
        self._write_meta_best_effort()

        logger.info(
            f"✅ user_db refreshed: {old_rows:,} -> {len(new_data):,} rows "
            f"({time.time() - t_start:.1f}s, source={source_url})"
        )
        return "ok"

    # -------------------------------------------------------- disk helpers

    def _write_snapshot_atomic(self, data: Dict[int, Union[str, tuple]]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._snapshot_path.with_suffix(".json.tmp")
        # JSON keys must be strings; stringify ids. Compact separators save ~20%.
        payload = {str(k): v for k, v in data.items()}
        with open(tmp, "w") as f:
            json.dump(payload, f, separators=(",", ":"))
        os.replace(tmp, self._snapshot_path)

    def _write_meta_best_effort(self) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._meta_path.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(asdict(self._meta), f, indent=2)
            os.replace(tmp, self._meta_path)
        except Exception as e:
            logger.debug(f"Could not write user_db meta file: {e}")


# ====================================================================
# Helpers — exposed at module level so scripts can import them
# ====================================================================

def filter_rows_from_csv_bytes(body: bytes, filter_cfg: dict) -> Dict[int, str]:
    """
    Parse CSV bytes (already gzip-decoded if applicable) and return a filtered
    {radio_id: callsign} dict. Pure function — no I/O, no state.

    filter_cfg keys (all optional):
        countries: list of exact COUNTRY strings, or the string "all"
            (default ["United States", "Canada"])
        callsign_regex: Python regex string applied to stripped callsign,
            or null
        radio_id_ranges: list of [low, high] inclusive int pairs, or null
    """
    return _filter_rows_from_csv_bytes(body, filter_cfg, include_profile=False)


def filter_profile_rows_from_csv_bytes(body: bytes, filter_cfg: dict) -> Dict[int, Union[str, tuple]]:
    """Keep only the directory columns approved for public identification."""
    return _filter_rows_from_csv_bytes(body, filter_cfg, include_profile=True)


def _directory_field(value: str, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _filter_rows_from_csv_bytes(body: bytes, filter_cfg: dict, include_profile: bool):
    countries = filter_cfg.get("countries", ["United States", "Canada"])
    allow_any_country = countries == "all"
    country_set = set(countries) if not allow_any_country else set()

    callsign_regex_raw = filter_cfg.get("callsign_regex")
    callsign_re = re.compile(callsign_regex_raw) if callsign_regex_raw else None

    id_ranges_raw = filter_cfg.get("radio_id_ranges")
    id_ranges: List[Tuple[int, int]] = []
    if id_ranges_raw:
        for pair in id_ranges_raw:
            try:
                lo, hi = int(pair[0]), int(pair[1])
                id_ranges.append((lo, hi) if lo <= hi else (hi, lo))
            except (TypeError, ValueError, IndexError):
                continue

    text = body.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    required = {"RADIO_ID", "CALLSIGN", "COUNTRY"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError(
            f"CSV missing required columns {required}; got {reader.fieldnames!r}"
        )

    out = {}
    for row in reader:
        if not allow_any_country:
            if row.get("COUNTRY", "").strip() not in country_set:
                continue

        callsign = (row.get("CALLSIGN") or "").strip()
        if not callsign:
            continue

        if callsign_re and not callsign_re.match(callsign):
            continue

        try:
            radio_id = int(row["RADIO_ID"])
        except (ValueError, KeyError, TypeError):
            continue

        if id_ranges:
            if not any(lo <= radio_id <= hi for lo, hi in id_ranges):
                continue

        if include_profile:
            first = _directory_field(row.get("FIRST_NAME"), 40)
            last = _directory_field(row.get("LAST_NAME"), 40)
            name = " ".join(part for part in (first, last) if part)[:80]
            city = _directory_field(row.get("CITY"), 60)
            region = _directory_field(row.get("STATE"), 40)
            out[radio_id] = (callsign, name, city, region) if name or city or region else callsign
        else:
            out[radio_id] = callsign

    return out


def _http_get_with_conditional(
    url: str,
    user_agent: str,
    if_modified_since: Optional[str] = None,
) -> Tuple[int, bytes, Optional[str]]:
    """
    Blocking HTTPS GET. Returns (status, body_bytes, last_modified_header).
    Handles gzip transparently. Raises on network/timeout; returns non-200
    statuses normally so the caller can branch.
    """
    req = urllib.request.Request(url)
    req.add_header("User-Agent", user_agent)
    req.add_header("Accept-Encoding", "gzip, identity")
    if if_modified_since:
        req.add_header("If-Modified-Since", if_modified_since)

    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TOTAL_TIMEOUT) as resp:
            status = resp.status
            last_modified = resp.headers.get("Last-Modified")
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"response exceeds {_MAX_RESPONSE_BYTES} bytes — refusing to process"
                )
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return status, raw, last_modified
    except urllib.error.HTTPError as e:
        # 304 and other status codes surface here; body is usually small/empty.
        lm = e.headers.get("Last-Modified") if e.headers else None
        try:
            body = e.read()
        except Exception:
            body = b""
        return e.code, body, lm


# ====================================================================
# Scheduling — one helper the server-side task reuses
# ====================================================================

def compute_next_refresh_seconds(
    schedule: str,
    time_of_day: str,
    jitter_minutes: int,
    now: Optional[datetime] = None,
) -> float:
    """
    Seconds to sleep before the next scheduled refresh. `schedule` is "daily"
    or "weekly"; anything else clamps to "daily". `time_of_day` is "HH:MM"
    local time. `jitter_minutes` adds [0, jitter_minutes) minutes of random
    offset so a fleet of dashboards don't all fire on the exact same second.
    """
    if schedule not in ("daily", "weekly"):
        logger.warning(
            f"⚠️ user_db refresh.schedule={schedule!r} is unsupported; clamping to 'daily'"
        )
        schedule = "daily"

    now = now or datetime.now()
    try:
        hour, minute = [int(x) for x in time_of_day.split(":", 1)]
    except (ValueError, AttributeError):
        logger.warning(
            f"⚠️ user_db refresh.time_of_day={time_of_day!r} unparseable; using 03:17"
        )
        hour, minute = 3, 17

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        # Already passed today; schedule next cycle
        days = 7 if schedule == "weekly" else 1
        target = target.replace(day=target.day)  # keep day; timedelta below
        from datetime import timedelta
        target = target + timedelta(days=days)

    jitter = random.uniform(0, max(0, jitter_minutes) * 60.0)
    delay = (target - now).total_seconds() + jitter
    return max(delay, 1.0)


def _age_str(ts: float) -> str:
    """'4h 12m', '3d 2h', or 'never'."""
    if not ts:
        return "never"
    seconds = max(0, int(time.time() - ts))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h {minutes % 60}m"
    days = hours // 24
    return f"{days}d {hours % 24}h"
