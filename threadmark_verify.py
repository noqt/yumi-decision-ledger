#!/usr/bin/env python3
"""Compare an explicitly supplied handoff card with a freshly resolved ledger.

This is an offline consistency check, not a verifier of truth or authority.
The card is untrusted input: its digest claims never replace recomputing the
current Threadmark with the adjacent ``yumi_continuity.py`` resolver.

Run with::

    python3 -I -B -S threadmark_verify.py check --card CARD.json --ledger LEDGER.json

Only bounded JSON is read, and one bounded JSON result is written to stdout.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA = "yumi.continuity.threadmark.verify.v1"
CLAIMS = {
    "card_authentication": "not_performed",
    "ledger_truth_freshness_or_completeness": "not_established",
    "permission_or_safety": "not_established",
    "input_values": "data_only_never_executed",
    "source_ids": "opaque_data_only_never_dereferenced",
}
_STATE_FIELDS = (
    "required",
    "status",
    "values",
    "heads",
    "assertion_ids",
    "revision_ids",
    "source_ids",
    "withdrawals",
)


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


def _parse_json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)


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


def _digest_claim(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _nonnegative_int(value: Any, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _valid_string_array(value: Any, *, maximum: int, max_length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= maximum
        and all(
            isinstance(item, str) and item.strip() and len(item) <= max_length
            for item in value
        )
        and len(set(value)) == len(value)
    )


def _card_errors(card: Any, raw: bytes, handoff: Any, resolver: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(card, dict):
        return ["root_object_required"]
    if card.get("resolver") != "yumi-continuity":
        errors.append("resolver")
    if card.get("schema") != "yumi.continuity.threadmark.handoff.v1.1":
        errors.append("schema")
    if card.get("mode") != "handoff_card":
        errors.append("mode")

    status = card.get("status")
    if not isinstance(status, str) or status not in {"READY", "REVIEW", "INVALID", "DETAIL_REQUIRED"}:
        errors.append("status")
    if card.get("decision") != status:
        errors.append("decision")
    if type(card.get("ready")) is not bool or card.get("ready") != (status == "READY"):
        errors.append("ready")
    if type(card.get("detail_required")) is not bool or card.get("detail_required") != (status == "DETAIL_REQUIRED"):
        errors.append("detail_required")
    if type(card.get("details_retained")) is not bool or card.get("details_retained") != (status != "DETAIL_REQUIRED"):
        errors.append("details_retained")

    scope = card.get("scope")
    if scope is not None and (not isinstance(scope, str) or len(scope) > handoff.MAX_SCOPE_LENGTH):
        errors.append("scope")

    receipt_digest = card.get("receipt_sha256")
    receipt_size = card.get("receipt_bytes")
    if receipt_digest is not None and not _digest_claim(receipt_digest):
        errors.append("receipt_sha256")
    if receipt_size is not None and not _nonnegative_int(receipt_size, handoff.MAX_INPUT_BYTES):
        errors.append("receipt_bytes")
    if (receipt_digest is None) != (receipt_size is None):
        errors.append("receipt_link")

    input_digest = card.get("input_sha256")
    input_size = card.get("input_bytes")
    if input_digest is not None and not _digest_claim(input_digest):
        errors.append("input_sha256")
    if input_size is not None and not _nonnegative_int(input_size, resolver.THREADMARK_MAX_INPUT_BYTES):
        errors.append("input_bytes")

    status = card.get("status")
    if status == "DETAIL_REQUIRED":
        # The existing exporter deliberately emits a detail-free bounded card.
        return sorted(set(errors))

    declared_heads = card.get("heads")
    if (
        not _valid_string_array(
            declared_heads,
            maximum=resolver.THREADMARK_MAX_HEADS,
            max_length=handoff.MAX_ID_LENGTH,
        )
        or (status == "READY" and not declared_heads)
    ):
        errors.append("heads")
    for field in ("assertion_ids", "revision_ids", "source_ids"):
        if not _valid_string_array(
            card.get(field),
            maximum=resolver.THREADMARK_MAX_ASSERTIONS,
            max_length=handoff.MAX_ID_LENGTH,
        ):
            errors.append(field)

    required = card.get("required_keys")
    if (
        not isinstance(required, list)
        or not 1 <= len(required) <= resolver.THREADMARK_MAX_KEYS
        or any(
            not isinstance(key, str)
            or not key.strip()
            or len(key) > handoff.MAX_ID_LENGTH
            for key in required
        )
        or len(set(required)) != len(required)
    ):
        errors.append("required_keys")
        return sorted(set(errors))

    raw_states = card.get("key_states")
    if not isinstance(raw_states, dict):
        errors.append("key_states")
        return sorted(set(errors))
    if set(raw_states) != set(required):
        errors.append("key_state_keys")

    normalized = handoff.export_handoff(card, raw_bytes=raw)
    if normalized.get("status") != status:
        errors.append("status_state_mismatch")
    normalized_states = normalized.get("key_states")
    if not isinstance(normalized_states, dict):
        errors.append("normalized_key_states")
        return sorted(set(errors))
    if set(normalized_states) != set(required):
        errors.append("normalized_key_state_keys")

    for key in required:
        state = normalized_states.get(key)
        if not isinstance(state, dict):
            errors.append(f"key_states.{key}")
            continue
        for value_index, value in enumerate(state.get("values", [])):
            if not isinstance(value, dict) or "value" not in value:
                errors.append(f"key_states.{key}.values[{value_index}]")
                continue
            try:
                expected_type = resolver._tm_type(value["value"])
                _canonical_bytes(value["value"])
            except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
                errors.append(f"key_states.{key}.values[{value_index}].value")
                continue
            if value.get("value_type") != expected_type:
                errors.append(f"key_states.{key}.values[{value_index}].value_type")
        if status == "READY" and state.get("status") != "stable":
            errors.append(f"key_states.{key}.status")
        if status == "READY" and not state.get("values"):
            errors.append(f"key_states.{key}.values")
        if status == "READY":
            observations = state.get("heads")
            observed_ids = [
                item.get("head_id") for item in observations if isinstance(item, dict)
            ] if isinstance(observations, list) else []
            declared_ids = card.get("heads")
            if (
                not _valid_string_array(
                    declared_ids,
                    maximum=resolver.THREADMARK_MAX_HEADS,
                    max_length=handoff.MAX_ID_LENGTH,
                )
                or not declared_ids
                or not isinstance(observations, list)
                or len(observed_ids) != len(observations)
                or len(set(observed_ids)) != len(observed_ids)
                or set(observed_ids) != set(declared_ids)
            ):
                errors.append(f"key_states.{key}.heads_declared_set")

    return sorted(set(errors))


def _state_projection(state: dict[str, Any]) -> dict[str, Any]:
    projection = {
        field: state.get(field, [] if field not in {"required", "status"} else None)
        for field in _STATE_FIELDS
    }
    # The resolver and card exporter treat these arrays as sets.  Sorting by
    # canonical JSON keeps comparison order-independent without Python's
    # bool/int equality coercion.
    for field in ("values", "heads", "assertion_ids", "revision_ids", "source_ids", "withdrawals"):
        members = projection[field]
        if isinstance(members, list):
            projection[field] = sorted(members, key=_canonical_bytes)
    return projection


def _state_hash(state: dict[str, Any]) -> str:
    return _sha256(_canonical_bytes(_state_projection(state)))


def _value_match(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return _canonical_bytes(_state_projection(before)["values"]) == _canonical_bytes(
        _state_projection(after)["values"]
    )


def _provenance_match(before: dict[str, Any], after: dict[str, Any]) -> bool:
    fields = ("heads", "assertion_ids", "revision_ids", "source_ids", "withdrawals")
    before_projection = _state_projection(before)
    after_projection = _state_projection(after)
    before_value = {field: before_projection[field] for field in fields}
    after_value = {field: after_projection[field] for field in fields}
    return _canonical_bytes(before_value) == _canonical_bytes(after_value)


def _key_drift(card: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    old_required = set(card.get("required_keys", []))
    new_required = set(current.get("required_keys", []))
    old_states = card.get("key_states", {})
    new_states = current.get("key_states", {})
    if not isinstance(old_states, dict) or not isinstance(new_states, dict):
        return []
    drift: list[dict[str, Any]] = []
    for key in sorted(old_required | new_required):
        before = old_states.get(key) if key in old_required else None
        after = new_states.get(key)
        current_required = key in new_required
        if not isinstance(after, dict):
            after = None
        entry: dict[str, Any] = {
            "key": key,
            "previous_required": key in old_required,
            "current_required": current_required,
            "previous_status": before.get("status") if isinstance(before, dict) else "not_in_card",
            "current_status": after.get("status") if after is not None else "not_observed",
            "matches": False,
            "values_match": False,
            "provenance_match": False,
            "previous_state_sha256": None,
            "current_state_sha256": None,
        }
        if isinstance(before, dict):
            entry["previous_state_sha256"] = _state_hash(before)
        if after is not None:
            entry["current_state_sha256"] = _state_hash(after)
        if isinstance(before, dict) and after is not None:
            entry["values_match"] = _value_match(before, after)
            entry["provenance_match"] = _provenance_match(before, after)
            entry["matches"] = _canonical_bytes(_state_projection(before)) == _canonical_bytes(_state_projection(after))
        drift.append(entry)
    return drift


def _base_result(
    *,
    status: str,
    reason: str,
    card_raw: bytes | None = None,
    ledger_raw: bytes | None = None,
    card_complete: bool = True,
    ledger_complete: bool = True,
    card: Any = None,
    ledger_result: dict[str, Any] | None = None,
    handoff: Any = None,
) -> dict[str, Any]:
    card_digest = _sha256(card_raw) if card_raw is not None and card_complete else None
    ledger_digest = _sha256(ledger_raw) if ledger_raw is not None and ledger_complete else None
    raw_claimed_receipt = card.get("receipt_sha256") if isinstance(card, dict) else None
    raw_claimed_receipt_bytes = card.get("receipt_bytes") if isinstance(card, dict) else None
    raw_claimed_input = card.get("input_sha256") if isinstance(card, dict) else None
    raw_claimed_input_bytes = card.get("input_bytes") if isinstance(card, dict) else None
    claimed_receipt = raw_claimed_receipt if _digest_claim(raw_claimed_receipt) else None
    claimed_receipt_bytes = (
        raw_claimed_receipt_bytes
        if handoff is not None
        and _nonnegative_int(raw_claimed_receipt_bytes, handoff.MAX_INPUT_BYTES)
        else None
    )
    claimed_input = raw_claimed_input if _digest_claim(raw_claimed_input) else None
    claimed_input_bytes = (
        raw_claimed_input_bytes
        if _nonnegative_int(raw_claimed_input_bytes, 64 * 1024)
        else None
    )
    digest_matches = None
    size_matches = None
    if claimed_input is not None and ledger_digest is not None:
        digest_matches = claimed_input.lower() == ledger_digest
    if claimed_input_bytes is not None and ledger_raw is not None and ledger_complete:
        size_matches = claimed_input_bytes == len(ledger_raw)

    capability = None
    if handoff is not None:
        try:
            capability = {
                "producer": "Yumi",
                "capability": "handoff-freshness-check",
                "version": "1.2",
                "doc_url": "https://github.com/noqt/yumi-decision-ledger",
                "inert": True,
                "base_capability": handoff._capability(),
            }
        except Exception:
            capability = None
    if capability is None and isinstance(ledger_result, dict):
        capability = ledger_result.get("capability")

    return {
        "schema": SCHEMA,
        "mode": "freshness_check",
        "status": status,
        "reason": reason,
        "card": {
            "sha256": card_digest,
            "bytes": len(card_raw) if card_raw is not None and card_complete else None,
            "complete": card_raw is not None and card_complete,
        },
        "ledger": {
            "sha256": ledger_digest,
            "bytes": len(ledger_raw) if ledger_raw is not None and ledger_complete else None,
            "complete": ledger_raw is not None and ledger_complete,
            "resolver_status": ledger_result.get("status") if isinstance(ledger_result, dict) else None,
        },
        "provenance_links": {
            "claimed_receipt_sha256": claimed_receipt,
            "claimed_receipt_bytes": claimed_receipt_bytes,
            "claimed_input_sha256": claimed_input,
            "claimed_input_bytes": claimed_input_bytes,
            "claimed_input_digest_matches_supplied_ledger": digest_matches,
            "claimed_input_bytes_match_supplied_ledger": size_matches,
            "claims_authenticated": False,
        },
        "scope_matches": None,
        "required_keys_match": None,
        "heads_match": None,
        "key_drift": [],
        "capability": capability,
        "limits": {
            "card_input_bytes": handoff.MAX_OUTPUT_BYTES if handoff is not None else 4096,
            "ledger_input_bytes": ledger_result.get("limits", {}).get("max_input_bytes", 65536)
            if isinstance(ledger_result, dict)
            else 65536,
            "result_bytes": 16384,
        },
        "claims": dict(CLAIMS),
    }


def _invalid_result(reason: str, **kwargs: Any) -> dict[str, Any]:
    return _base_result(status="INVALID", reason=reason, **kwargs)


def _run(card_path: str, ledger_path: str) -> tuple[dict[str, Any], int]:
    try:
        resolver = _load_sibling("_threadmark_verify_yumi_continuity", "yumi_continuity.py")
        handoff = _load_sibling("_threadmark_verify_handoff", "threadmark_handoff.py")
    except Exception as exc:
        return _invalid_result(f"module_unavailable:{type(exc).__name__}"), 2

    if card_path == "-" and ledger_path == "-":
        return _invalid_result("both_inputs_cannot_share_stdin", handoff=handoff), 2

    try:
        card_raw = _read_bounded(card_path, handoff.MAX_OUTPUT_BYTES)
        ledger_raw = _read_bounded(ledger_path, resolver.THREADMARK_MAX_INPUT_BYTES)
    except (OSError, ValueError) as exc:
        return _invalid_result(f"input_read_failed:{type(exc).__name__}", handoff=handoff), 2

    card_complete = len(card_raw) <= handoff.MAX_OUTPUT_BYTES
    ledger_complete = len(ledger_raw) <= resolver.THREADMARK_MAX_INPUT_BYTES
    if not card_complete or not ledger_complete:
        result = _invalid_result(
            "input_limit",
            card_raw=card_raw,
            ledger_raw=ledger_raw,
            card_complete=card_complete,
            ledger_complete=ledger_complete,
            handoff=handoff,
        )
        result["limits"]["overflow"] = "card" if not card_complete else "ledger"
        result["limits"]["observed_card_bytes"] = len(card_raw)
        result["limits"]["observed_ledger_bytes"] = len(ledger_raw)
        return result, 2

    try:
        card = _parse_json(card_raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        return _invalid_result(
            "card_json_invalid",
            card_raw=card_raw,
            ledger_raw=ledger_raw,
            handoff=handoff,
        ), 2
    try:
        ledger = _parse_json(ledger_raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        return _invalid_result(
            "ledger_json_invalid",
            card_raw=card_raw,
            ledger_raw=ledger_raw,
            card=card,
            handoff=handoff,
        ), 2

    errors = _card_errors(card, card_raw, handoff, resolver)
    if errors:
        result = _invalid_result(
            "card_contract_invalid",
            card_raw=card_raw,
            ledger_raw=ledger_raw,
            card=card,
            handoff=handoff,
        )
        result["validation_errors"] = errors[:32]
        return result, 2

    current = resolver.threadmark_ledger(ledger, raw_bytes=ledger_raw)
    if not isinstance(current, dict) or current.get("status") == "INVALID":
        result = _invalid_result(
            "ledger_invalid",
            card_raw=card_raw,
            ledger_raw=ledger_raw,
            card=card,
            ledger_result=current if isinstance(current, dict) else None,
            handoff=handoff,
        )
        blockers = current.get("blockers", []) if isinstance(current, dict) else []
        result["ledger_blockers"] = [
            item.get("code", "invalid") for item in blockers[:16] if isinstance(item, dict)
        ]
        return result, 2

    result = _base_result(
        status="BLOCKED",
        reason="comparison_incomplete",
        card_raw=card_raw,
        ledger_raw=ledger_raw,
        card=card,
        ledger_result=current,
        handoff=handoff,
    )
    if current.get("status") not in {"READY", "REVIEW"}:
        result["reason"] = "ledger_detail_unavailable"
        result["ledger_blockers"] = [
            item.get("code", "blocked")
            for item in current.get("blockers", [])[:16]
            if isinstance(item, dict)
        ]
        return result, 1

    normalized = handoff.export_handoff(card, raw_bytes=card_raw)
    result["scope_matches"] = card.get("scope") == current.get("scope")
    result["required_keys_match"] = sorted(card.get("required_keys", [])) == sorted(
        current.get("required_keys", [])
    )
    result["heads_match"] = sorted(card.get("heads", [])) == sorted(current.get("heads", []))
    result["key_drift"] = _key_drift(normalized, current)

    if card.get("status") != "READY":
        result["reason"] = "card_not_ready"
    elif current.get("status") != "READY":
        result["reason"] = "ledger_not_ready"
    elif not result["scope_matches"]:
        result["status"] = "STALE"
        result["reason"] = "scope_changed"
    elif not result["required_keys_match"]:
        result["status"] = "STALE"
        result["reason"] = "required_keys_changed"
    elif not result["heads_match"]:
        result["status"] = "STALE"
        result["reason"] = "declared_heads_changed"
    elif any(not item["matches"] for item in result["key_drift"]):
        result["status"] = "STALE"
        result["reason"] = "key_state_changed"
    else:
        result["status"] = "UNCHANGED"
        result["reason"] = "required_key_snapshot_matches"

    try:
        encoded = _canonical_bytes(result)
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return _invalid_result(
            "result_encoding_failed",
            card_raw=card_raw,
            ledger_raw=ledger_raw,
            card=card,
            ledger_result=current,
            handoff=handoff,
        ), 2
    if len(encoded) > resolver.THREADMARK_MAX_OUTPUT_BYTES:
        compact = _base_result(
            status="BLOCKED",
            reason="result_limit",
            card_raw=card_raw,
            ledger_raw=ledger_raw,
            card=card,
            ledger_result=current,
            handoff=handoff,
        )
        compact["scope_matches"] = result["scope_matches"]
        compact["required_keys_match"] = result["required_keys_match"]
        compact["heads_match"] = result["heads_match"]
        compact["key_drift"] = result["key_drift"][:8]
        return compact, 1
    return result, 0 if result["status"] == "UNCHANGED" else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if (
        len(args) != 5
        or args[0] != "check"
        or args.count("--card") != 1
        or args.count("--ledger") != 1
        or args[1] not in {"--card", "--ledger"}
        or args[3] not in {"--card", "--ledger"}
        or args[1] == args[3]
    ):
        result = _invalid_result("usage: check --card PATH --ledger PATH")
        sys.stdout.write(_canonical_bytes(result).decode("utf-8") + "\n")
        return 2
    paths = {args[1]: args[2], args[3]: args[4]}
    card_path = paths["--card"]
    ledger_path = paths["--ledger"]
    if not card_path or not ledger_path:
        result = _invalid_result("usage: check --card PATH --ledger PATH")
        sys.stdout.write(_canonical_bytes(result).decode("utf-8") + "\n")
        return 2
    try:
        result, code = _run(card_path, ledger_path)
    except Exception as exc:
        result, code = _invalid_result(f"internal_error:{type(exc).__name__}"), 2
    try:
        encoded = _canonical_bytes(result).decode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        fallback = _invalid_result("result_encoding_failed")
        encoded = json.dumps(
            fallback,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        code = 2
    sys.stdout.write(encoded + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
