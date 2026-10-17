#!/usr/bin/env python3
"""Compare an explicit settings snapshot with a freshly resolved ledger.

This is an offline applicability check, not an authentication or truth check.
The supplied ledger is resolved by the adjacent ``yumi_continuity.py`` module;
the bridge never chooses a head or infers authority from file order.

Run with::

    python3 -I -B -S snapshot_check.py check --snapshot SNAPSHOT.json --ledger LEDGER.json
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA = "yumi.continuity.snapshot.applicability.v1"
MODE = "snapshot_applicability_check"
MAX_INPUT_BYTES = 64 * 1024
MAX_RESULT_BYTES = 16 * 1024
CLAIMS = {
    "snapshot_authentication": "not_performed",
    "ledger_truth_freshness_or_completeness": "not_established",
    "permission_or_safety": "not_established",
    "task_completion": "not_established",
    "input_values": "data_only_never_executed",
    "source_ids": "opaque_data_only_never_dereferenced",
}


class _DuplicateJSONKey(ValueError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey("duplicate object key")
        result[key] = value
    return result


def _parse_json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"),
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_object,
    )


def _read_bounded(path: str, limit: int) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read(limit + 1)
    with open(path, "rb") as handle:
        return handle.read(limit + 1)


def _load_sibling(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("sibling module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_text(value: Any, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
    )


def _string_array(value: Any, *, maximum: int, max_length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= maximum
        and all(_valid_text(item, max_length) for item in value)
        and len(set(value)) == len(value)
    )


def _snapshot_errors(snapshot: Any, resolver: Any) -> list[str]:
    if not isinstance(snapshot, dict):
        return ["root_object_required"]
    errors: list[str] = []
    try:
        _canonical_bytes(snapshot)
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        errors.append("snapshot_json_data")
    if set(snapshot) != {"scope", "settings"}:
        errors.append("root_fields")
    if not _valid_text(snapshot.get("scope"), resolver.MAX_ID_LENGTH):
        errors.append("scope")
    settings = snapshot.get("settings")
    if not isinstance(settings, dict):
        errors.append("settings_object_required")
        return sorted(set(errors))
    for key, value in settings.items():
        if not _valid_text(key, resolver.MAX_ID_LENGTH):
            errors.append("settings_key")
            continue
        try:
            resolver._tm_typed_signature(value)
            _canonical_bytes(value)
        except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
            errors.append("settings_value")
    return sorted(set(errors))


def _identifier_array(value: Any, maximum: int, *, max_length: int = 256) -> bool:
    return _string_array(value, maximum=maximum, max_length=max_length)


def _validate_key_state(key: str, state: Any, resolver: Any, required: bool) -> bool:
    if not isinstance(state, dict) or state.get("key") != key:
        return False
    if type(state.get("required")) is not bool or state.get("required") != required:
        return False
    if state.get("status") not in {"stable", "missing", "withdrawn", "conflicting"}:
        return False
    values = state.get("values")
    if not isinstance(values, list) or len(values) > resolver.THREADMARK_MAX_ASSERTIONS:
        return False
    try:
        for item in values:
            if not isinstance(item, dict) or "value" not in item:
                return False
            resolver._tm_typed_signature(item["value"])
            _canonical_bytes(item["value"])
            if item.get("value_type") != resolver._tm_type(item["value"]):
                return False
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return False
    observations = state.get("heads")
    if not isinstance(observations, list) or len(observations) > resolver.THREADMARK_MAX_HEADS:
        return False
    observed_heads: list[str] = []
    for item in observations:
        if not isinstance(item, dict) or not _valid_text(item.get("head_id"), resolver.MAX_ID_LENGTH):
            return False
        if not _identifier_array(item.get("assertion_ids"), resolver.THREADMARK_MAX_ASSERTIONS):
            return False
        for field in ("live_count", "withdrawal_count"):
            count = item.get(field)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                return False
        observed_heads.append(item["head_id"])
    if len(set(observed_heads)) != len(observed_heads):
        return False
    for field in ("assertion_ids", "revision_ids"):
        if not _identifier_array(state.get(field), resolver.THREADMARK_MAX_ASSERTIONS):
            return False
    if not _identifier_array(
        state.get("source_ids"),
        resolver.THREADMARK_MAX_ASSERTIONS,
        max_length=resolver.THREADMARK_MAX_INPUT_BYTES,
    ):
        return False
    return True


def _resolver_error(current: Any, resolver: Any) -> str | None:
    if not isinstance(current, dict):
        return "resolver_result_not_object"
    try:
        _canonical_bytes(current)
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return "resolver_result_not_json"
    if (
        current.get("resolver") != "yumi-continuity"
        or current.get("schema") != "yumi.continuity.threadmark.v1.1"
        or current.get("mode") != "threadmark"
    ):
        return "resolver_identity_invalid"
    status = current.get("status")
    if status == "INVALID":
        return "ledger_invalid"
    if status == "DETAIL_REQUIRED":
        return None
    if status not in {"READY", "REVIEW"}:
        return "resolver_status_invalid"
    if (
        not _valid_text(current.get("scope"), resolver.MAX_ID_LENGTH)
        or not _identifier_array(current.get("required_keys"), resolver.THREADMARK_MAX_KEYS)
        or not 1 <= len(current.get("required_keys", [])) <= resolver.THREADMARK_MAX_KEYS
        or not _identifier_array(current.get("heads"), resolver.THREADMARK_MAX_HEADS)
        or not current.get("heads")
    ):
        return "resolver_summary_invalid"
    states = current.get("key_states")
    if not isinstance(states, dict):
        return "resolver_key_states_invalid"
    required = set(current["required_keys"])
    for key in required:
        state = states.get(key)
        if not _validate_key_state(key, state, resolver, True):
            return "resolver_key_state_invalid"
        if {item["head_id"] for item in state["heads"]} != set(current["heads"]):
            return "resolver_head_observations_invalid"
        if status == "READY":
            if state["status"] != "stable" or len(state["values"]) != 1:
                return "resolver_ready_state_invalid"
            if any(item["live_count"] < 1 for item in state["heads"]):
                return "resolver_ready_head_invalid"
    if status == "READY" and (current.get("ready") is not True or current.get("decision") != "READY"):
        return "resolver_ready_marker_invalid"
    if status == "REVIEW" and (current.get("ready") is not False or current.get("decision") != "REVIEW"):
        return "resolver_review_marker_invalid"
    return None


def _capability(current: Any = None) -> dict[str, Any]:
    base = {
        "producer": "Yumi",
        "capability": "continuity-threadmark",
        "version": "1.1",
        "doc_url": "https://github.com/noqt/noqt/tree/main/yumi/continuity/v1.1",
        "inert": True,
    }
    if isinstance(current, dict) and isinstance(current.get("capability"), dict):
        supplied = current["capability"]
        if all(supplied.get(key) == value for key, value in base.items()):
            base = {key: supplied[key] for key in base}
    return {
        "producer": "Yumi",
        "capability": "snapshot-applicability-check",
        "version": "1.0",
        "doc_url": "https://github.com/noqt/yumi-decision-ledger",
        "inert": True,
        "base_capability": base,
    }


def _input_record(raw: bytes | None, complete: bool, limit: int) -> dict[str, Any]:
    return {
        "sha256": _sha256(raw) if raw is not None and complete else None,
        "bytes": len(raw) if raw is not None else None,
        "complete": raw is not None and complete and len(raw) <= limit,
    }


def _base_result(
    *,
    status: str,
    reason: str,
    snapshot_raw: bytes | None = None,
    ledger_raw: bytes | None = None,
    snapshot_complete: bool = False,
    ledger_complete: bool = False,
    current: Any = None,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "mode": MODE,
        "status": status,
        "reason": reason,
        "inputs": {
            "snapshot": _input_record(snapshot_raw, snapshot_complete, MAX_INPUT_BYTES),
            "ledger": _input_record(ledger_raw, ledger_complete, MAX_INPUT_BYTES),
        },
        "resolver_status": current.get("status") if isinstance(current, dict) else None,
        "scope_matches": None,
        "settings_keys_match": None,
        "key_drift": [],
        "capability": _capability(current),
        "limits": {
            "snapshot_input_bytes": MAX_INPUT_BYTES,
            "ledger_input_bytes": MAX_INPUT_BYTES,
            "resolver_required_keys": 8,
            "scope_and_key_length": 256,
            "result_bytes": MAX_RESULT_BYTES,
        },
        "claims": dict(CLAIMS),
    }


def _key_drift(snapshot: dict[str, Any], current: dict[str, Any], resolver: Any) -> list[dict[str, Any]]:
    settings = snapshot["settings"]
    previous_keys = set(settings)
    current_keys = set(current.get("required_keys", []))
    states = current.get("key_states", {})
    drift: list[dict[str, Any]] = []
    for key in sorted(previous_keys | current_keys):
        previous_present = key in settings
        state = states.get(key) if isinstance(states, dict) else None
        if not isinstance(state, dict):
            state = None
        values = state.get("values", []) if state is not None else []
        if not isinstance(values, list):
            values = []
        withdrawals = state.get("withdrawals", []) if state is not None else []
        if not isinstance(withdrawals, list):
            withdrawals = []
        current_source_ids = set(state.get("source_ids", [])) if state is not None else set()
        current_source_ids.update(
            item["source_id"]
            for item in withdrawals
            if isinstance(item, dict) and isinstance(item.get("source_id"), str)
        )
        entry: dict[str, Any] = {
            "key": key,
            "snapshot_present": previous_present,
            "current_required": key in current_keys,
            "snapshot_value": settings.get(key),
            "snapshot_value_type": resolver._tm_type(settings[key]) if previous_present else None,
            "current_status": state.get("status") if state is not None else "not_observed",
            "current_values": values,
            "current_heads": state.get("heads", []) if state is not None else [],
            "assertion_ids": state.get("assertion_ids", []) if state is not None else [],
            "revision_ids": state.get("revision_ids", []) if state is not None else [],
            "source_ids": sorted(current_source_ids),
            "withdrawals": withdrawals,
            "value_matches": None,
        }
        if (
            previous_present
            and state is not None
            and state.get("status") == "stable"
            and len(values) == 1
            and isinstance(values[0], dict)
            and "value" in values[0]
        ):
            entry["value_matches"] = (
                resolver._tm_typed_signature(settings[key])
                == resolver._tm_typed_signature(values[0]["value"])
            )
        drift.append(entry)
    return drift


def _fallback_detail(result: dict[str, Any]) -> dict[str, Any]:
    compact = dict(result)
    compact["status"] = "BLOCKED"
    compact["reason"] = "detail_required"
    compact["details_complete"] = False
    compact["key_drift"] = [
        {
            "key": item["key"],
            "snapshot_present": item["snapshot_present"],
            "current_required": item["current_required"],
            "current_status": item["current_status"],
            "details_complete": False,
        }
        for item in result.get("key_drift", [])
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    ]
    return compact


def _run(snapshot_path: str, ledger_path: str) -> tuple[dict[str, Any], int]:
    try:
        resolver = _load_sibling("_snapshot_check_yumi_continuity", "yumi_continuity.py")
    except Exception as exc:
        return _base_result(status="INVALID", reason=f"module_unavailable:{type(exc).__name__}"), 2

    if snapshot_path == "-" and ledger_path == "-":
        return _base_result(status="INVALID", reason="both_inputs_cannot_share_stdin"), 2

    snapshot_raw: bytes | None = None
    ledger_raw: bytes | None = None
    snapshot_complete = False
    ledger_complete = False
    read_errors: list[str] = []
    try:
        snapshot_raw = _read_bounded(snapshot_path, MAX_INPUT_BYTES)
        snapshot_complete = len(snapshot_raw) <= MAX_INPUT_BYTES
    except (OSError, ValueError):
        read_errors.append("snapshot_read_failed")
    try:
        ledger_raw = _read_bounded(ledger_path, MAX_INPUT_BYTES)
        ledger_complete = len(ledger_raw) <= MAX_INPUT_BYTES
    except (OSError, ValueError):
        read_errors.append("ledger_read_failed")
    if read_errors:
        result = _base_result(
            status="INVALID",
            reason="input_read_failed",
            snapshot_raw=snapshot_raw,
            ledger_raw=ledger_raw,
            snapshot_complete=snapshot_complete,
            ledger_complete=ledger_complete,
        )
        result["input_errors"] = read_errors
        return result, 2

    if not snapshot_complete or not ledger_complete:
        result = _base_result(
            status="INVALID",
            reason="input_limit",
            snapshot_raw=snapshot_raw,
            ledger_raw=ledger_raw,
            snapshot_complete=snapshot_complete,
            ledger_complete=ledger_complete,
        )
        result["overflow"] = [
            name
            for name, complete in (("snapshot", snapshot_complete), ("ledger", ledger_complete))
            if not complete
        ]
        return result, 2

    snapshot: Any = None
    ledger: Any = None
    snapshot_parse_error = False
    ledger_parse_error = False
    try:
        snapshot = _parse_json(snapshot_raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        snapshot_parse_error = True
    try:
        ledger = _parse_json(ledger_raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        ledger_parse_error = True
    if ledger_parse_error:
        return _base_result(
            status="INVALID",
            reason="ledger_json_invalid",
            snapshot_raw=snapshot_raw,
            ledger_raw=ledger_raw,
            snapshot_complete=True,
            ledger_complete=True,
        ), 2

    try:
        current = resolver.threadmark_ledger(ledger, raw_bytes=ledger_raw)
    except Exception as exc:
        return _base_result(
            status="INVALID",
            reason=f"resolver_error:{type(exc).__name__}",
            snapshot_raw=snapshot_raw,
            ledger_raw=ledger_raw,
            snapshot_complete=True,
            ledger_complete=True,
        ), 2

    resolver_problem = _resolver_error(current, resolver)
    if resolver_problem is not None:
        result = _base_result(
            status="INVALID",
            reason=resolver_problem,
            snapshot_raw=snapshot_raw,
            ledger_raw=ledger_raw,
            snapshot_complete=True,
            ledger_complete=True,
            current=current,
        )
        if isinstance(current, dict) and isinstance(current.get("blockers"), list):
            result["ledger_blockers"] = [
                item.get("code", "invalid")
                for item in current["blockers"][:16]
                if isinstance(item, dict) and isinstance(item.get("code"), str)
            ]
        return result, 2

    if snapshot_parse_error:
        return _base_result(
            status="INVALID",
            reason="snapshot_json_invalid",
            snapshot_raw=snapshot_raw,
            ledger_raw=ledger_raw,
            snapshot_complete=True,
            ledger_complete=True,
            current=current,
        ), 2

    snapshot_errors = _snapshot_errors(snapshot, resolver)
    if snapshot_errors:
        result = _base_result(
            status="INVALID",
            reason="snapshot_contract_invalid",
            snapshot_raw=snapshot_raw,
            ledger_raw=ledger_raw,
            snapshot_complete=True,
            ledger_complete=True,
            current=current,
        )
        result["validation_errors"] = snapshot_errors[:16]
        return result, 2

    result = _base_result(
        status="BLOCKED",
        reason="ledger_not_ready",
        snapshot_raw=snapshot_raw,
        ledger_raw=ledger_raw,
        snapshot_complete=True,
        ledger_complete=True,
        current=current,
    )
    if current["status"] == "DETAIL_REQUIRED":
        result["reason"] = "ledger_detail_required"
        result["details_complete"] = False
        return result, 1

    result["scope_matches"] = snapshot["scope"] == current["scope"]
    result["settings_keys_match"] = set(snapshot["settings"]) == set(current["required_keys"])
    result["key_drift"] = _key_drift(snapshot, current, resolver)
    if current["status"] == "REVIEW":
        result["reason"] = "ledger_not_ready"
    elif not result["scope_matches"]:
        result["status"] = "STALE"
        result["reason"] = "scope_changed"
    elif not result["settings_keys_match"]:
        result["status"] = "STALE"
        result["reason"] = "required_keys_changed"
    elif any(item["value_matches"] is not True for item in result["key_drift"]):
        result["status"] = "STALE"
        result["reason"] = "setting_value_changed"
    else:
        result["status"] = "UNCHANGED"
        result["reason"] = "snapshot_settings_match"

    try:
        encoded = _canonical_bytes(result)
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return _base_result(
            status="INVALID",
            reason="result_encoding_failed",
            snapshot_raw=snapshot_raw,
            ledger_raw=ledger_raw,
            snapshot_complete=True,
            ledger_complete=True,
            current=current,
        ), 2
    if len(encoded) > MAX_RESULT_BYTES:
        compact = _fallback_detail(result)
        try:
            compact_bytes = _canonical_bytes(compact)
        except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
            compact_bytes = b""
        if compact_bytes and len(compact_bytes) <= MAX_RESULT_BYTES:
            return compact, 1
        fallback = _base_result(
            status="BLOCKED",
            reason="detail_required",
            snapshot_raw=snapshot_raw,
            ledger_raw=ledger_raw,
            snapshot_complete=True,
            ledger_complete=True,
            current=current,
        )
        fallback["scope_matches"] = result["scope_matches"]
        fallback["settings_keys_match"] = result["settings_keys_match"]
        fallback["details_complete"] = False
        return fallback, 1
    return result, 0 if result["status"] == "UNCHANGED" else 1


def _usage_result() -> dict[str, Any]:
    return _base_result(
        status="INVALID",
        reason="usage: check --snapshot PATH --ledger PATH",
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if (
        len(args) != 5
        or args[0] != "check"
        or args.count("--snapshot") != 1
        or args.count("--ledger") != 1
        or args[1] not in {"--snapshot", "--ledger"}
        or args[3] not in {"--snapshot", "--ledger"}
        or args[1] == args[3]
    ):
        result, code = _usage_result(), 2
    else:
        paths = {args[1]: args[2], args[3]: args[4]}
        snapshot_path = paths["--snapshot"]
        ledger_path = paths["--ledger"]
        if not snapshot_path or not ledger_path:
            result, code = _usage_result(), 2
        else:
            try:
                result, code = _run(snapshot_path, ledger_path)
            except Exception as exc:
                result, code = _base_result(
                    status="INVALID",
                    reason=f"internal_error:{type(exc).__name__}",
                ), 2
    try:
        encoded = _canonical_bytes(result)
        if len(encoded) > MAX_RESULT_BYTES:
            result = _base_result(status="INVALID", reason="result_limit_fallback")
            encoded = _canonical_bytes(result)
            code = 2
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        result = _base_result(status="INVALID", reason="result_encoding_failed")
        encoded = _canonical_bytes(result)
        code = 2
    sys.stdout.write(encoded.decode("utf-8") + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
