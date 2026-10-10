"""BuenoDMR operator validation and HBlink4 pattern generation."""

import copy
import json
import re

from hblink4.access_control import RepeaterMatcher
from hblink4.config import validate_config


SHARED_SECRET_KEY = "_bueno_shared_passphrase"
CALLSIGN = re.compile(r"^[A-Z]{1,3}[0-9][A-Z]{1,5}$")
FIELDS = ("callsign", "base_id", "essid_from", "essid_to", "active")


class OperatorValidationError(ValueError):
    pass


def validate_operator(payload, *, create=False):
    if not isinstance(payload, dict) or set(payload) - set(FIELDS):
        raise OperatorValidationError("Unexpected operator fields.")
    required = {"callsign", "base_id"} if create else set(FIELDS)
    if not required <= set(payload):
        raise OperatorValidationError("Required operator fields are missing.")
    callsign = payload["callsign"]
    if not isinstance(callsign, str):
        raise OperatorValidationError("Invalid callsign.")
    callsign = callsign.strip().upper()
    if not CALLSIGN.fullmatch(callsign):
        raise OperatorValidationError("Invalid callsign.")
    base_id = payload["base_id"]
    if type(base_id) is not int or not 1000000 <= base_id <= 9999999:
        raise OperatorValidationError("Base DMR ID must have exactly seven digits.")
    start = payload.get("essid_from", 0)
    end = payload.get("essid_to", 99)
    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= 99:
        raise OperatorValidationError("ESSID must be between 00 and 99, with start <= end.")
    active = payload.get("active", True)
    if type(active) is not bool:
        raise OperatorValidationError("Active must be true or false.")
    return {"callsign": callsign, "base_id": base_id, "essid_from": start,
            "essid_to": end, "active": active}


def operator_range(operator):
    base = operator["base_id"] * 100
    return base + operator["essid_from"], base + operator["essid_to"]


def validate_collection(operators):
    seen_callsigns = set()
    seen_bases = set()
    ranges = []
    for operator in operators:
        data = validate_operator({key: operator[key] for key in FIELDS})
        if data["callsign"] in seen_callsigns:
            raise OperatorValidationError("Callsign already exists.")
        if data["base_id"] in seen_bases:
            raise OperatorValidationError("Base DMR ID already exists.")
        start, end = operator_range(data)
        if any(start <= other_end and other_start <= end for other_start, other_end in ranges):
            raise OperatorValidationError("DMR ID range overlaps an existing operator.")
        seen_callsigns.add(data["callsign"])
        seen_bases.add(data["base_id"])
        ranges.append((start, end))


def parse_config_operators(config):
    """Accept only unambiguous BuenoDMR ID ranges and one shared passphrase."""
    section = config.get("repeater_configurations")
    if not isinstance(section, dict) or "default" in section:
        raise OperatorValidationError("The repeater configuration has an unsupported default.")
    if set(section) - {"patterns", SHARED_SECRET_KEY, "_comment"}:
        raise OperatorValidationError("The repeater configuration contains unsupported fields.")
    patterns = section.get("patterns")
    if not isinstance(patterns, list):
        raise OperatorValidationError("The repeater patterns are invalid.")
    shared = section.get(SHARED_SECRET_KEY)
    if shared is not None and (not isinstance(shared, str) or not shared):
        raise OperatorValidationError("The shared DMR passphrase is unavailable.")
    operators = []
    for pattern in patterns:
        if not isinstance(pattern, dict) or set(pattern) - {"name", "description", "match", "config"}:
            raise OperatorValidationError("A repeater pattern is ambiguous.")
        match = pattern.get("match")
        policy = pattern.get("config")
        if (not isinstance(match, dict) or set(match) != {"id_ranges"}
                or not isinstance(match["id_ranges"], list) or len(match["id_ranges"]) != 1
                or not isinstance(match["id_ranges"][0], list)
                or len(match["id_ranges"][0]) != 2
                or not isinstance(policy, dict)):
            raise OperatorValidationError("A repeater pattern is ambiguous.")
        start, end = match["id_ranges"][0]
        if (type(start) is not int or type(end) is not int or start // 100 != end // 100
                or set(policy) - {"passphrase", "trust", "slot1_talkgroups",
                                  "slot2_talkgroups", "default_unit_calls"}
                or policy.get("trust", False) is not False
                or policy.get("slot1_talkgroups") != []
                or policy.get("slot2_talkgroups") != [100]
                or policy.get("default_unit_calls", False) is not False):
            raise OperatorValidationError("A repeater pattern has an unsupported access policy.")
        password = policy.get("passphrase")
        if not isinstance(password, str) or not password or (shared is not None and password != shared):
            raise OperatorValidationError("Repeater patterns do not share one passphrase.")
        if shared is None:
            shared = password
        operators.append(validate_operator({
            "callsign": pattern.get("name"), "base_id": start // 100,
            "essid_from": start % 100, "essid_to": end % 100, "active": True,
        }))
    validate_collection(operators)
    if shared is None:
        raise OperatorValidationError("The shared DMR passphrase is unavailable.")
    return operators, shared


def public_operator(operator):
    start, end = operator_range(operator)
    return {"id": operator["id"], "callsign": operator["callsign"],
            "base_id": operator["base_id"], "essid_from": operator["essid_from"],
            "essid_to": operator["essid_to"], "range_start": start, "range_end": end,
            "active": operator["active"]}


def build_config(current, operators):
    """Replace only the ACL section; keep the passphrase inside the live config."""
    _, shared = parse_config_operators(current)
    validate_collection(operators)
    candidate = copy.deepcopy(current)
    patterns = []
    for operator in operators:
        if not operator["active"]:
            continue
        start, end = operator_range(operator)
        patterns.append({"name": operator["callsign"],
                         "match": {"id_ranges": [[start, end]]},
                         "config": {"passphrase": shared, "trust": False,
                                    "slot1_talkgroups": [], "slot2_talkgroups": [100]}})
    candidate["repeater_configurations"] = {"patterns": patterns, SHARED_SECRET_KEY: shared}
    encoded = json.dumps(candidate, ensure_ascii=False, indent=4) + "\n"
    decoded = json.loads(encoded)
    if not validate_config(decoded):
        raise OperatorValidationError("Generated HBlink4 configuration failed validation.")
    if {k: v for k, v in current.items() if k != "repeater_configurations"} != {
            k: v for k, v in decoded.items() if k != "repeater_configurations"}:
        raise OperatorValidationError("Unrelated configuration changed.")
    matched = RepeaterMatcher(decoded)
    if matched.default_config is not None or len(matched.patterns) != len(patterns):
        raise OperatorValidationError("Generated ACL failed matcher validation.")
    for operator in operators:
        start, end = operator_range(operator)
        for rid in range(start, end + 1):
            result = matched.get_repeater_config(rid, operator["callsign"])
            if (result is None) == operator["active"]:
                raise OperatorValidationError("Generated ACL failed ID matching.")
    for rid in (0, 999999999):
        if not any(operator["active"] and operator_range(operator)[0] <= rid <= operator_range(operator)[1]
                   for operator in operators) and matched.get_repeater_config(rid, "PY2DES") is not None:
            raise OperatorValidationError("Generated ACL admits an unknown ID.")
    return encoded


def config_matches_operators(config, operators):
    configured, _ = parse_config_operators(config)
    expected = [{key: row[key] for key in FIELDS} for row in operators if row["active"]]
    return configured == expected
