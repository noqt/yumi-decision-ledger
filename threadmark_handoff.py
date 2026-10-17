#!/usr/bin/env python3
"""Export an explicitly supplied Threadmark receipt as a small handoff card.

This module is deliberately a receipt exporter, not another ledger resolver.
It reads JSON that a caller supplies, retains the required-key states and
their typed values/references, and emits a canonical bounded JSON card.  It
never opens a source reference, recomputes a ledger, authenticates anything,
or treats a receipt as truth, permission, safety, or access.

The command-line form is::

    python3 -I -B -S threadmark_handoff.py export --input receipt.json

``handoff`` and ``card`` are accepted as aliases for ``export``.  ``-`` reads
the receipt from stdin.  The output is written to stdout only.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any


MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 4 * 1024
MAX_REQUIRED_KEYS = 8
MAX_ID_LENGTH = 256
MAX_SCOPE_LENGTH = 256
# Descriptive aliases make the bounds obvious to callers without creating a
# second configuration surface.
MAX_RECEIPT_BYTES = MAX_INPUT_BYTES
MAX_CARD_BYTES = MAX_OUTPUT_BYTES

DOC_URL = "https://github.com/noqt/noqt/tree/main/yumi/continuity/v1.1"
CAPABILITY = {
    "producer": "Yumi",
    "capability": "continuity-threadmark",
    "version": "1.1",
    "doc_url": DOC_URL,
    "inert": True,
}
CLAIMS = (
    "This card summarizes an explicitly supplied receipt only; it does not "
    "establish truth, authentication, completeness, permission, safety, "
    "freshness, or access."
)

_STATES = frozenset({"stable", "missing", "withdrawn", "conflicting"})
_STATUSES = frozenset({"READY", "REVIEW", "INVALID", "DETAIL_REQUIRED"})


def _capability() -> dict[str, Any]:
    return dict(CAPABILITY)


def canonical(value: Any) -> str:
    """Return deterministic JSON for a JSON-compatible value."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_bytes(value: Any) -> bytes:
    return canonical(value).encode("utf-8")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_sort(items: list[Any]) -> list[Any]:
    """Sort JSON members without changing their typed values."""

    try:
        return sorted(items, key=canonical)
    except (TypeError, ValueError, RecursionError):
        return items


def _strings(value: Any, *, max_length: int = MAX_ID_LENGTH) -> tuple[list[str], bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return [], False
    result: list[str] = []
    valid = True
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > max_length:
            valid = False
            continue
        result.append(item)
    return sorted(set(result)), valid


def _optional_strings(value: Any) -> list[str]:
    result, _ = _strings(value)
    return result


def _status(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in _STATUSES else None


def _input_metadata(receipt: Any, raw_bytes: bytes | None) -> tuple[str | None, int | None]:
    if raw_bytes is not None:
        return _digest(raw_bytes), len(raw_bytes)
    try:
        raw = canonical_bytes(receipt)
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return None, None
    return _digest(raw), len(raw)


def _empty_state(key: str, status: str = "missing") -> dict[str, Any]:
    return {
        "key": key,
        "required": True,
        "status": status,
        "values": [],
        "heads": [],
        "assertion_ids": [],
        "revision_ids": [],
        "source_ids": [],
        "withdrawals": [],
    }


def _head_observations(raw: Any, errors: list[str], key: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"key_states.{key}.heads")
        return []
    output: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"key_states.{key}.heads[{index}]")
            continue
        head_id = item.get("head_id", item.get("id"))
        if not isinstance(head_id, str) or not head_id or len(head_id) > MAX_ID_LENGTH:
            errors.append(f"key_states.{key}.heads[{index}].head_id")
            continue
        assertion_ids, assertions_valid = _strings(item.get("assertion_ids", []))
        if not assertions_valid:
            errors.append(f"key_states.{key}.heads[{index}].assertion_ids")
        observation: dict[str, Any] = {
            "head_id": head_id,
            "assertion_ids": assertion_ids,
        }
        for field in ("live_count", "withdrawal_count"):
            value = item.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(f"key_states.{key}.heads[{index}].{field}")
            else:
                observation[field] = value
        output.append(observation)
    return sorted(output, key=canonical)


def _values(raw: Any, errors: list[str], key: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"key_states.{key}.values")
        return []
    output: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or "value" not in item:
            errors.append(f"key_states.{key}.values[{index}]")
            continue
        value_type = item.get("value_type")
        if not isinstance(value_type, str) or not value_type.strip():
            errors.append(f"key_states.{key}.values[{index}].value_type")
            continue
        # Copy the value exactly.  In particular, do not stringify 1, 1.0,
        # true, arrays, objects, or null while making a compact card.
        output.append({"value": item["value"], "value_type": value_type})
    return _safe_sort(output)


def _withdrawals(raw: Any, errors: list[str], key: str) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"key_states.{key}.withdrawals")
        return []
    copied: list[Any] = []
    for index, item in enumerate(raw):
        try:
            canonical(item)
        except (TypeError, ValueError, RecursionError):
            errors.append(f"key_states.{key}.withdrawals[{index}]")
            continue
        copied.append(item)
    return _safe_sort(copied)


def _state_for(
    key: str,
    raw: Any,
    errors: list[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_state(key)

    state = raw.get("status", raw.get("state"))
    if not isinstance(state, str) or state.strip().lower() not in _STATES:
        errors.append(f"key_states.{key}.status")
        state_name = "missing"
    else:
        state_name = state.strip().lower()
    values = _values(raw.get("values", []), errors, key)
    if state_name in {"stable", "conflicting"} and not values:
        errors.append(f"key_states.{key}.values")
    heads = _head_observations(raw.get("heads", []), errors, key)
    assertion_ids, assertions_valid = _strings(raw.get("assertion_ids", []))
    revision_ids, revisions_valid = _strings(raw.get("revision_ids", []))
    source_raw = raw.get("source_ids", raw.get("source_refs", []))
    source_ids, sources_valid = _strings(source_raw)
    if not assertions_valid:
        errors.append(f"key_states.{key}.assertion_ids")
    if not revisions_valid:
        errors.append(f"key_states.{key}.revision_ids")
    if not sources_valid:
        errors.append(f"key_states.{key}.source_ids")
    return {
        "key": key,
        "required": True,
        "status": state_name,
        "values": values,
        "heads": heads,
        "assertion_ids": assertion_ids,
        "revision_ids": revision_ids,
        "source_ids": source_ids,
        "withdrawals": _withdrawals(raw.get("withdrawals", []), errors, key),
    }


def _refs_from_states(states: dict[str, dict[str, Any]], field: str) -> list[str]:
    return sorted(
        {
            item
            for state in states.values()
            for item in state.get(field, [])
            if isinstance(item, str)
        }
    )


def _heads_from_states(states: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        {
            item.get("head_id")
            for state in states.values()
            for item in state.get("heads", [])
            if isinstance(item, dict) and isinstance(item.get("head_id"), str)
        }
    )


def _blockers(receipt: dict[str, Any], states: dict[str, dict[str, Any]]) -> list[Any]:
    supplied = receipt.get("blockers", [])
    result: list[Any] = []
    if isinstance(supplied, list):
        for item in supplied:
            try:
                canonical(item)
            except (TypeError, ValueError, RecursionError):
                continue
            result.append(item)
    known = {canonical(item) for item in result}
    for key in sorted(states):
        state = states[key]
        if state["status"] == "stable":
            continue
        item = {
            "code": state["status"],
            "key": key,
            "required": True,
            "assertion_ids": state["assertion_ids"],
            "revision_ids": state["revision_ids"],
            "source_ids": state["source_ids"],
        }
        signature = canonical(item)
        if signature not in known:
            result.append(item)
            known.add(signature)
    return _safe_sort(result)


def _fallback(
    *,
    receipt_sha256: str | None,
    receipt_bytes: int | None,
    input_sha256: Any = None,
    input_bytes: Any = None,
    scope: Any = None,
    required_keys: list[str] | None = None,
    original_status: str | None = None,
) -> dict[str, Any]:
    keys = required_keys if required_keys is not None else []
    # The fallback is intentionally detail-free.  It is the honest signal
    # that a complete required-key card cannot fit; it never claims READY.
    return {
        "resolver": "yumi-continuity",
        "schema": "yumi.continuity.threadmark.handoff.v1.1",
        "mode": "handoff_card",
        "status": "DETAIL_REQUIRED",
        "decision": "DETAIL_REQUIRED",
        "ready": False,
        "detail_required": True,
        "details_retained": False,
        "original_status": original_status,
        "scope": scope if isinstance(scope, str) and len(scope) <= MAX_SCOPE_LENGTH else None,
        "receipt_sha256": receipt_sha256,
        "receipt_bytes": receipt_bytes,
        "input_sha256": input_sha256 if isinstance(input_sha256, str) else None,
        "input_bytes": input_bytes if isinstance(input_bytes, int) and not isinstance(input_bytes, bool) else None,
        "required_keys": keys if len(keys) <= MAX_REQUIRED_KEYS else [],
        "required_key_count": len(keys),
        "capability": _capability(),
        "limits": {"max_input_bytes": MAX_INPUT_BYTES, "max_output_bytes": MAX_OUTPUT_BYTES},
        "claims": CLAIMS,
        "blockers": [{"code": "output_limit", "required": True}],
    }


def _invalid_input(metadata: dict[str, Any], code: str) -> dict[str, Any]:
    result = _fallback(**metadata)
    result.update(
        {
            "status": "INVALID",
            "decision": "INVALID",
            "detail_required": False,
            "details_retained": True,
            "blockers": [{"code": code, "required": True}],
        }
    )
    return result


def _bounded(result: dict[str, Any], *, metadata: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = canonical_bytes(result)
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        encoded = None
    if encoded is not None and len(encoded) <= MAX_OUTPUT_BYTES:
        return result
    fallback = _fallback(**metadata)
    # The fixed fallback is designed to fit.  Keep this last guard explicit so
    # a future documentation edit cannot accidentally emit an unbounded card.
    if len(canonical_bytes(fallback)) <= MAX_OUTPUT_BYTES:
        return fallback
    return {
        "schema": "yumi.continuity.threadmark.handoff.v1.1",
        "status": "DETAIL_REQUIRED",
        "ready": False,
        "detail_required": True,
    }


def export_handoff(receipt: Any, *, raw_bytes: bytes | None = None) -> dict[str, Any]:
    """Build a bounded card from a supplied Threadmark receipt.

    ``raw_bytes`` should be the exact bytes read from the receipt when the
    caller wants ``receipt_sha256`` to describe that byte stream.  When it is
    omitted, the canonical JSON representation is hashed instead.  This is a
    transport digest only; it is not authentication or ledger verification.
    """

    receipt_sha256, receipt_bytes = _input_metadata(receipt, raw_bytes)
    metadata = {
        "receipt_sha256": receipt_sha256,
        "receipt_bytes": receipt_bytes,
        "input_sha256": receipt.get("input_sha256") if isinstance(receipt, dict) else None,
        "input_bytes": receipt.get("input_bytes") if isinstance(receipt, dict) else None,
        "scope": receipt.get("scope") if isinstance(receipt, dict) else None,
        "required_keys": [],
        "original_status": None,
    }
    if raw_bytes is not None and len(raw_bytes) > MAX_INPUT_BYTES:
        return _invalid_input(metadata, "input_limit")
    if not isinstance(receipt, dict):
        metadata["original_status"] = "INVALID"
        return _bounded(
            {
                **_fallback(**metadata),
                "status": "INVALID",
                "decision": "INVALID",
                "detail_required": False,
                "details_retained": True,
                "blockers": [{"code": "root_object_required", "required": True}],
            },
            metadata=metadata,
        )

    errors: list[str] = []
    receipt_status = _status(receipt.get("status", receipt.get("decision")))
    metadata["original_status"] = receipt_status
    scope = receipt.get("scope")
    if scope is not None and (not isinstance(scope, str) or len(scope) > MAX_SCOPE_LENGTH):
        errors.append("scope")
        scope = None
    required_raw = receipt.get("required_keys", receipt.get("required", receipt.get("keys")))
    required_keys, required_valid = _strings(required_raw)
    metadata["required_keys"] = required_keys
    if not required_valid or not required_keys or len(required_keys) > MAX_REQUIRED_KEYS:
        errors.append("required_keys")

    raw_states = receipt.get("key_states", receipt.get("states", {}))
    if not isinstance(raw_states, dict):
        errors.append("key_states")
        raw_states = {}
    states: dict[str, dict[str, Any]] = {}
    for key in required_keys:
        states[key] = _state_for(key, raw_states.get(key), errors)

    # A supplied receipt may expose a non-required state, but a compact handoff
    # card is intentionally limited to the caller's required keys.  Nothing
    # outside that set is treated as a current fact.
    status_names = {state["status"] for state in states.values()}
    if receipt_status is None:
        errors.append("status")
        card_status = "INVALID"
    elif receipt_status == "DETAIL_REQUIRED":
        card_status = "DETAIL_REQUIRED"
    elif errors:
        card_status = "INVALID"
    elif receipt_status == "INVALID":
        card_status = "INVALID"
    elif receipt_status == "READY" and status_names <= {"stable"}:
        card_status = "READY"
    elif receipt_status in {"READY", "REVIEW"}:
        card_status = "REVIEW"
    else:
        card_status = "INVALID"

    assertion_ids = _optional_strings(receipt.get("assertion_ids")) or _refs_from_states(states, "assertion_ids")
    revision_ids = _optional_strings(receipt.get("revision_ids")) or _refs_from_states(states, "revision_ids")
    source_ids = _optional_strings(receipt.get("source_ids")) or _refs_from_states(states, "source_ids")
    heads = _optional_strings(receipt.get("heads")) or _heads_from_states(states)
    blockers = _blockers(receipt, states)
    if errors:
        blockers.extend({"code": "invalid_receipt", "detail": value, "required": True} for value in sorted(set(errors)))
        blockers = _safe_sort(blockers)

    result = {
        "resolver": "yumi-continuity",
        "schema": "yumi.continuity.threadmark.handoff.v1.1",
        "mode": "handoff_card",
        "status": card_status,
        "decision": card_status,
        "ready": card_status == "READY",
        "detail_required": card_status == "DETAIL_REQUIRED",
        "details_retained": card_status != "DETAIL_REQUIRED",
        "scope": scope,
        "receipt_sha256": receipt_sha256,
        "receipt_bytes": receipt_bytes,
        "input_sha256": receipt.get("input_sha256"),
        "input_bytes": receipt.get("input_bytes"),
        "required_keys": required_keys,
        "heads": heads,
        "assertion_ids": assertion_ids,
        "revision_ids": revision_ids,
        "source_ids": source_ids,
        "key_states": states,
        "blockers": blockers,
        "capability": _capability(),
        "provenance_policy": {
            "source_ids": "opaque_data_only",
            "source_labels": "unverified",
            "values": "data_only_never_executed",
        },
        "limits": {"max_input_bytes": MAX_INPUT_BYTES, "max_output_bytes": MAX_OUTPUT_BYTES},
        "claims": CLAIMS,
    }
    return _bounded(result, metadata=metadata)


# Friendly names for callers that prefer the noun used in the public docs.
build_handoff_card = export_handoff
handoff_card = export_handoff
export_receipt = export_handoff
export_threadmark = export_handoff
build_card = export_handoff
handoff = export_handoff


def _cli_input(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    with open(path, "rb") as handle:
        return handle.read(MAX_INPUT_BYTES + 1)


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON constant: {value}")


def _cli_failure(code: str, *, raw: bytes | None = None) -> dict[str, Any]:
    digest = _digest(raw) if raw is not None else None
    size = len(raw) if raw is not None else None
    return _fallback(
        receipt_sha256=digest,
        receipt_bytes=size,
        original_status="INVALID",
    ) | {
        "status": "INVALID",
        "decision": "INVALID",
        "detail_required": False,
        "details_retained": True,
        "blockers": [{"code": code, "required": True}],
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"export", "handoff", "card", "handoff-card"}:
        args.pop(0)
    if "--output" in args:
        result = _cli_failure("unsupported_option:--output")
        sys.stdout.write(canonical(result) + "\n")
        return 2
    if "--input" not in args or args.count("--input") != 1:
        result = _cli_failure("usage: export --input PATH_OR_DASH")
        sys.stdout.write(canonical(result) + "\n")
        return 2
    index = args.index("--input")
    if index + 1 >= len(args) or len(args) != 2:
        result = _cli_failure("usage: export --input PATH_OR_DASH")
        sys.stdout.write(canonical(result) + "\n")
        return 2
    path = args[index + 1]
    try:
        raw = _cli_input(path)
    except (OSError, ValueError) as exc:
        result = _cli_failure(f"input_read_failed:{type(exc).__name__}")
        sys.stdout.write(canonical(result) + "\n")
        return 2
    if len(raw) > MAX_INPUT_BYTES:
        result = _invalid_input(
            {
                "receipt_sha256": _digest(raw),
                "receipt_bytes": len(raw),
                "original_status": "INVALID",
            },
            "input_limit",
        )
        sys.stdout.write(canonical(result) + "\n")
        return 2
    try:
        receipt = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        result = _cli_failure("invalid_json", raw=raw)
        sys.stdout.write(canonical(result) + "\n")
        return 2
    result = export_handoff(receipt, raw_bytes=raw)
    sys.stdout.write(canonical(result) + "\n")
    return 0 if result.get("status") == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
