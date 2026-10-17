"""Small, local execution receipt prototype. Receipts are evidence records, not authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any


SCHEMA_V1 = "origin-result-receipt/v1"
SCHEMA_V2 = "origin-result-receipt/v2"
SCHEMA = SCHEMA_V2
SOFTWARE_VERSION = "1.1.0"
PRODUCER_FIELDS = {
    "name": 64,
    "software_version": 64,
    "repository": 512,
    "format_documentation": 512,
}
PRODUCER_METADATA = {
    "name": "origin-receipt",
    "software_version": SOFTWARE_VERSION,
    "repository": "https://github.com/noqt/yumi-decision-ledger",
    "format_documentation": "origin-receipt.json",
}
MAX_CAPTURE_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SCOPE_ITEMS = 128
MAX_PATH_CHARS = 1024
MAX_ARG_CHARS = 4096
MAX_TOTAL_ARG_CHARS = 64 * 1024
MAX_TIMEOUT_SECONDS = 180
HEX_256 = re.compile(r"^[0-9a-f]{64}$")


class ReceiptError(ValueError):
    pass


def _check_producer_metadata(metadata: Any) -> None:
    if not isinstance(metadata, dict) or set(metadata) != set(PRODUCER_FIELDS):
        raise ReceiptError("receipt producer metadata is malformed")
    for field, maximum in PRODUCER_FIELDS.items():
        value = metadata[field]
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ReceiptError(f"receipt producer field is malformed: {field}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReceiptError(f"invalid JSON constant: {value}")


def _is_reparse_or_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    # Windows reparse points include junctions and other path redirections.
    reparse_flag = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _workspace(raw: str) -> Path:
    if not raw or "\x00" in raw:
        raise ReceiptError("workspace must be an explicit path")
    given = Path(raw)
    try:
        root = given.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ReceiptError(f"workspace cannot be resolved: {type(exc).__name__}") from None
    if not root.is_dir():
        raise ReceiptError("workspace must be an existing directory")
    return root


def _relative(raw: Any) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > MAX_PATH_CHARS:
        raise ReceiptError("scope paths must be non-empty relative strings")
    if "\x00" in raw or "\\" in raw or ":" in raw or raw.startswith("/"):
        raise ReceiptError(f"unsafe or ambiguous relative path: {raw!r}")
    parts = raw.split("/")
    if any(part in ("", ".", "..") or not part.strip() for part in parts):
        raise ReceiptError(f"unsafe or ambiguous relative path: {raw!r}")
    return raw


def _scoped_path(root: Path, raw: Any, *, allow_missing: bool) -> Path:
    rel = _relative(raw)
    candidate = root.joinpath(*rel.split("/"))
    current = root
    missing_parent = False
    for component in rel.split("/"):
        current = current / component
        if missing_parent:
            continue
        if _is_reparse_or_link(current):
            raise ReceiptError(f"scope path contains a link/reparse point: {rel}")
        if not current.exists():
            missing_parent = True
    try:
        resolved = candidate.resolve(strict=not allow_missing)
    except (OSError, RuntimeError) as exc:
        raise ReceiptError(f"scope path cannot be resolved: {rel}: {type(exc).__name__}") from None
    try:
        inside = os.path.commonpath((os.fspath(root), os.fspath(resolved))) == os.fspath(root)
    except ValueError:
        inside = False
    if not inside:
        raise ReceiptError(f"scope path escapes workspace: {rel}")
    return candidate


def _scope_list(items: list[str] | None, label: str) -> list[str]:
    paths = [_relative(item) for item in (items or [])]
    if len(paths) > MAX_SCOPE_ITEMS or len(set(paths)) != len(paths):
        raise ReceiptError(f"{label} has too many or duplicate paths")
    return sorted(paths)


def _validate_scopes(inputs: list[str], artifacts: list[str], receipt_path: str | None) -> None:
    if set(inputs) & set(artifacts):
        raise ReceiptError("an input cannot also be an output artifact")
    if receipt_path and receipt_path in set(inputs) | set(artifacts):
        raise ReceiptError("receipt path cannot be an input or output artifact")


def _file_identity(root: Path, raw: str) -> dict[str, Any]:
    path = _scoped_path(root, raw, allow_missing=True)
    try:
        before = path.stat()
    except FileNotFoundError:
        return {"status": "missing"}
    if not path.is_file():
        return {"status": "not_regular_file"}
    if before.st_size > MAX_FILE_BYTES:
        return {"status": "too_large", "bytes": before.st_size}

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(64 * 1024)
            if not block:
                break
            size += len(block)
            if size > MAX_FILE_BYTES:
                return {"status": "too_large", "bytes": size}
            digest.update(block)
    after = path.stat()
    stable = (
        before.st_size == after.st_size == size
        and before.st_mtime_ns == after.st_mtime_ns
        and getattr(before, "st_ino", None) == getattr(after, "st_ino", None)
    )
    return {
        "status": "present",
        "bytes": size,
        "sha256": digest.hexdigest(),
        "stable_during_hash": stable,
    }


def _identity_map(root: Path, paths: list[str]) -> dict[str, dict[str, Any]]:
    return {path: _file_identity(root, path) for path in paths}


def _read_capped(pipe: Any, output: dict[str, Any], name: str) -> None:
    kept = bytearray()
    total = 0
    while True:
        chunk = pipe.read(8192)
        if not chunk:
            break
        total += len(chunk)
        room = MAX_CAPTURE_BYTES - len(kept)
        if room > 0:
            kept.extend(chunk[:room])
    output[name] = {
        "text": bytes(kept).decode("utf-8", errors="replace"),
        "bytes_total": total,
        "truncated": total > len(kept),
    }


def _capture_streams(proc: subprocess.Popen[bytes]) -> tuple[dict[str, Any], list[threading.Thread]]:
    result: dict[str, Any] = {}
    workers: list[threading.Thread] = []
    assert proc.stdout is not None and proc.stderr is not None
    for name, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        worker = threading.Thread(target=_read_capped, args=(pipe, result, name), daemon=False)
        worker.start()
        workers.append(worker)
    return result, workers


def _safe_argv(argv: list[str]) -> list[str]:
    if not argv or len(argv) > 256 or any(not isinstance(arg, str) for arg in argv):
        raise ReceiptError("command must be a non-empty argv list")
    total = sum(len(arg) for arg in argv)
    if total > MAX_TOTAL_ARG_CHARS or any("\x00" in arg or len(arg) > MAX_ARG_CHARS for arg in argv):
        raise ReceiptError("command arguments exceed the bounded receipt limits")
    return argv


def _atomic_receipt(root: Path, raw_path: str, receipt: dict[str, Any]) -> None:
    path = _scoped_path(root, raw_path, allow_missing=True)
    if path.exists():
        raise ReceiptError("refusing to overwrite an existing receipt")
    if not path.parent.is_dir():
        raise ReceiptError("receipt parent directory must already exist")
    envelope = {"body": receipt, "sha256": hashlib.sha256(_canonical(receipt)).hexdigest()}
    data = _canonical(envelope) + b"\n"
    if len(data) > MAX_RECEIPT_BYTES:
        raise ReceiptError("receipt exceeds the size limit")
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        raise ReceiptError("refusing to overwrite an existing receipt") from None


def run_command(
    workspace: str,
    receipt_path: str,
    inputs: list[str],
    artifacts: list[str],
    argv: list[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    """Execute argv once in the explicit workspace and persist its observed facts."""
    root = _workspace(workspace)
    receipt_rel = _relative(receipt_path)
    input_paths = _scope_list(inputs, "inputs")
    artifact_paths = _scope_list(artifacts, "artifacts")
    _validate_scopes(input_paths, artifact_paths, receipt_rel)
    command = _safe_argv(argv)
    if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ReceiptError(f"timeout must be greater than zero and at most {MAX_TIMEOUT_SECONDS} seconds")

    for rel in input_paths:
        path = _scoped_path(root, rel, allow_missing=True)
        if path.exists() and not path.is_file():
            raise ReceiptError(f"declared input is not a regular file: {rel}")
    for rel in artifact_paths:
        path = _scoped_path(root, rel, allow_missing=True)
        if path.exists() and not path.is_file():
            raise ReceiptError(f"declared artifact is not a regular file: {rel}")
    _scoped_path(root, receipt_rel, allow_missing=True)
    if (root / Path(*receipt_rel.split("/"))).exists():
        raise ReceiptError("refusing to overwrite an existing receipt")
    if not (root / Path(*receipt_rel.split("/"))).parent.is_dir():
        raise ReceiptError("receipt parent directory must already exist")

    before_inputs = _identity_map(root, input_paths)
    started_at = _utc_now()
    started_tick = time.monotonic()
    status = "completed"
    exit_code: int | None = None
    launch_error: dict[str, str] | None = None
    timed_out = False
    streams = {
        "stdout": {"text": "", "bytes_total": 0, "truncated": False},
        "stderr": {"text": "", "bytes_total": 0, "truncated": False},
    }
    proc: subprocess.Popen[bytes] | None = None
    workers: list[threading.Thread] = []
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            command,
            cwd=root,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            creationflags=creationflags,
        )
        streams, workers = _capture_streams(proc)
        try:
            exit_code = proc.wait(timeout=float(timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            status = "timed_out"
            proc.kill()
            exit_code = proc.wait()
        for worker in workers:
            worker.join()
    except OSError as exc:
        status = "launch_error"
        launch_error = {"type": type(exc).__name__, "message": str(exc)[:2048]}
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            exit_code = proc.wait()
        for worker in workers:
            if worker.is_alive():
                worker.join(timeout=5)
        if proc is not None:
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()

    finished_at = _utc_now()
    after_inputs = _identity_map(root, input_paths)
    after_artifacts: dict[str, dict[str, Any]] = {}
    for rel in artifact_paths:
        try:
            after_artifacts[rel] = _file_identity(root, rel)
        except ReceiptError:
            after_artifacts[rel] = {"status": "unsafe_path"}

    body: dict[str, Any] = {
        "schema": SCHEMA,
        "producer": dict(PRODUCER_METADATA),
        "run_id": uuid.uuid4().hex,
        "recorded_at_utc": finished_at,
        "scope": {
            "inputs": input_paths,
            "artifacts": artifact_paths,
            "binding": "BOUND" if input_paths else "UNBOUND",
        },
        "execution": {
            "argv": command,
            "status": status,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "launch_error": launch_error,
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
            "elapsed_ms": max(0, int((time.monotonic() - started_tick) * 1000)),
            "timeout_seconds": float(timeout_seconds),
            "stdout": streams["stdout"],
            "stderr": streams["stderr"],
        },
        "inputs_before": before_inputs,
        "inputs_after": after_inputs,
        "artifacts_after": after_artifacts,
    }
    _atomic_receipt(root, receipt_rel, body)
    return {
        "receipt": receipt_rel,
        "run_id": body["run_id"],
        "status": status,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "scope_binding": body["scope"]["binding"],
    }


def _check_body_shape(body: Any) -> None:
    required_v1 = {
        "schema", "run_id", "recorded_at_utc", "scope", "execution",
        "inputs_before", "inputs_after", "artifacts_after",
    }
    if not isinstance(body, dict):
        raise ReceiptError("receipt body has an unsupported or malformed schema")
    schema = body.get("schema")
    if schema == SCHEMA_V1:
        required = required_v1
    elif schema == SCHEMA_V2:
        required = required_v1 | {"producer"}
        _check_producer_metadata(body.get("producer"))
    else:
        raise ReceiptError("receipt body has an unsupported or malformed schema")
    if set(body) != required:
        raise ReceiptError("receipt body has an unsupported or malformed schema")
    if not isinstance(body["run_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", body["run_id"]):
        raise ReceiptError("receipt run_id is malformed")
    scope = body["scope"]
    if not isinstance(scope, dict) or set(scope) != {"inputs", "artifacts", "binding"}:
        raise ReceiptError("receipt scope is malformed")
    inputs = scope["inputs"]
    artifacts = scope["artifacts"]
    if not isinstance(inputs, list) or not isinstance(artifacts, list):
        raise ReceiptError("receipt scope lists are malformed")
    input_paths = _scope_list(inputs, "receipt inputs")
    artifact_paths = _scope_list(artifacts, "receipt artifacts")
    if inputs != input_paths or artifacts != artifact_paths:
        raise ReceiptError("receipt scope lists are not canonical")
    _validate_scopes(input_paths, artifact_paths, None)
    expected_binding = "BOUND" if inputs else "UNBOUND"
    if scope["binding"] != expected_binding:
        raise ReceiptError("receipt scope binding is inconsistent")

    execution = body["execution"]
    fields = {
        "argv", "status", "exit_code", "timed_out", "launch_error", "started_at_utc",
        "finished_at_utc", "elapsed_ms", "timeout_seconds", "stdout", "stderr",
    }
    if not isinstance(execution, dict) or set(execution) != fields:
        raise ReceiptError("receipt execution facts are malformed")
    argv = execution["argv"]
    if not isinstance(argv, list) or _safe_argv(argv) != argv:
        raise ReceiptError("receipt argv is malformed")
    if execution["status"] not in {"completed", "timed_out", "launch_error"}:
        raise ReceiptError("receipt execution status is malformed")
    code = execution["exit_code"]
    if code is not None and (not isinstance(code, int) or isinstance(code, bool)):
        raise ReceiptError("receipt exit code is malformed")
    if not isinstance(execution["timed_out"], bool):
        raise ReceiptError("receipt timeout fact is malformed")
    if not isinstance(execution["elapsed_ms"], int) or execution["elapsed_ms"] < 0:
        raise ReceiptError("receipt elapsed time is malformed")
    for name in ("stdout", "stderr"):
        capture = execution[name]
        if not isinstance(capture, dict) or set(capture) != {"text", "bytes_total", "truncated"}:
            raise ReceiptError(f"receipt {name} capture is malformed")
        if (
            not isinstance(capture["text"], str)
            or len(capture["text"].encode("utf-8")) > MAX_CAPTURE_BYTES * 3
            or not isinstance(capture["bytes_total"], int)
            or capture["bytes_total"] < 0
            or not isinstance(capture["truncated"], bool)
        ):
            raise ReceiptError(f"receipt {name} capture is malformed")
    if execution["status"] == "launch_error":
        err = execution["launch_error"]
        if not isinstance(err, dict) or set(err) != {"type", "message"}:
            raise ReceiptError("receipt launch error is malformed")
    elif execution["launch_error"] is not None:
        raise ReceiptError("receipt launch error is inconsistent")

    for map_name, expected_paths in (
        ("inputs_before", inputs), ("inputs_after", inputs), ("artifacts_after", artifacts)
    ):
        identities = body[map_name]
        if not isinstance(identities, dict) or set(identities) != set(expected_paths):
            raise ReceiptError(f"receipt {map_name} scope does not match")
        for value in identities.values():
            if not isinstance(value, dict) or "status" not in value:
                raise ReceiptError(f"receipt {map_name} identity is malformed")
            if value["status"] == "present":
                if (
                    set(value) != {"status", "bytes", "sha256", "stable_during_hash"}
                    or not isinstance(value["bytes"], int)
                    or value["bytes"] < 0
                    or not isinstance(value["sha256"], str)
                    or not HEX_256.fullmatch(value["sha256"])
                    or not isinstance(value["stable_during_hash"], bool)
                ):
                    raise ReceiptError(f"receipt {map_name} file identity is malformed")
            elif value["status"] not in {"missing", "not_regular_file", "too_large", "unsafe_path"}:
                raise ReceiptError(f"receipt {map_name} status is malformed")


def _load_receipt(root: Path, receipt_rel: str) -> dict[str, Any]:
    path = _scoped_path(root, receipt_rel, allow_missing=False)
    if not path.is_file():
        raise ReceiptError("receipt is not a regular file")
    if path.stat().st_size > MAX_RECEIPT_BYTES:
        raise ReceiptError("receipt exceeds the size limit")
    try:
        raw = path.read_bytes()
        envelope = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"receipt cannot be parsed: {type(exc).__name__}") from None
    if not isinstance(envelope, dict) or set(envelope) != {"body", "sha256"}:
        raise ReceiptError("receipt envelope is malformed")
    body = envelope["body"]
    if not isinstance(envelope["sha256"], str) or not HEX_256.fullmatch(envelope["sha256"]):
        raise ReceiptError("receipt digest is malformed")
    actual = hashlib.sha256(_canonical(body)).hexdigest()
    if actual != envelope["sha256"]:
        raise ReceiptError("receipt digest mismatch (tampered or corrupt)")
    _check_body_shape(body)
    return body


def _identity_is_verifiable(identity: dict[str, Any]) -> bool:
    return identity.get("status") == "present" and identity.get("stable_during_hash") is True


def check_receipt(
    workspace: str,
    receipt_path: str,
    inputs: list[str] | None,
    artifacts: list[str] | None,
) -> dict[str, Any]:
    """Read a receipt and workspace only. This function never launches processes."""
    root = _workspace(workspace)
    try:
        receipt_rel = _relative(receipt_path)
        body = _load_receipt(root, receipt_rel)
    except ReceiptError as exc:
        return {
            "receipt": str(receipt_path),
            "receipt_valid": False,
            "scope_state": "INVALID_RECEIPT",
            "differences": [],
            "error": str(exc),
        }
    stored_inputs = body["scope"]["inputs"]
    stored_artifacts = body["scope"]["artifacts"]
    current_inputs = _scope_list(inputs, "check inputs")
    current_artifacts = _scope_list(artifacts, "check artifacts")
    _validate_scopes(current_inputs, current_artifacts, receipt_rel)
    scope_matches = current_inputs == stored_inputs and current_artifacts == stored_artifacts
    declared_scope_state = "MATCH" if scope_matches else "MISMATCH"
    if body["scope"]["binding"] == "UNBOUND":
        declared_scope_state = "UNBOUND"

    differences: list[dict[str, str]] = []
    unverifiable_identity = False
    for rel in stored_inputs:
        before = body["inputs_before"][rel]
        after = body["inputs_after"][rel]
        try:
            current = _file_identity(root, rel)
        except ReceiptError:
            differences.append({"path": rel, "kind": "unsafe_or_invalid_path"})
            current = {"status": "unsafe_path"}
        if not all(_identity_is_verifiable(identity) for identity in (before, after, current)):
            differences.append({"path": rel, "kind": "unverifiable_identity"})
            unverifiable_identity = True
        if current["status"] == "missing":
            differences.append({"path": rel, "kind": "missing_input"})
        elif current["status"] != "unsafe_path" and current != after:
            differences.append({"path": rel, "kind": "changed_since_run"})
        if before != after:
            differences.append({"path": rel, "kind": "changed_during_run_window"})
    for rel in stored_artifacts:
        recorded = body["artifacts_after"][rel]
        try:
            current = _file_identity(root, rel)
        except ReceiptError:
            differences.append({"path": rel, "kind": "unsafe_or_invalid_path"})
            current = {"status": "unsafe_path"}
        if not all(_identity_is_verifiable(identity) for identity in (recorded, current)):
            differences.append({"path": rel, "kind": "unverifiable_identity"})
            unverifiable_identity = True
        if current["status"] == "missing":
            differences.append({"path": rel, "kind": "missing_artifact"})
        elif current["status"] != "unsafe_path" and current != recorded:
            differences.append({"path": rel, "kind": "changed_since_run"})

    if unverifiable_identity:
        scope_state = "INDETERMINATE"
    elif body["scope"]["binding"] == "UNBOUND":
        scope_state = "UNBOUND"
    elif not scope_matches or differences:
        scope_state = "MISMATCH"
    else:
        scope_state = "MATCH"

    return {
        "receipt": receipt_rel,
        "run_id": body["run_id"],
        "receipt_valid": True,
        "producer_metadata_status": "self_reported" if "producer" in body else "not_recorded",
        "producer_metadata": body.get("producer"),
        "producer_metadata_limit": "Informational strings; not authentication or authority.",
        "execution_status": body["execution"]["status"],
        "recorded_exit_code": body["execution"]["exit_code"],
        "recorded_timed_out": body["execution"]["timed_out"],
        "scope_state": scope_state,
        "declared_scope_state": declared_scope_state,
        "differences": differences,
        "interpretation_limit": "Hash agreement is not correctness, completeness, permission, external truth, or authority.",
    }


def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, help="explicit authorized lab workspace directory")
    parser.add_argument("--receipt", required=True, help="receipt path relative to workspace")
    parser.add_argument("--input", action="append", default=[], help="declared input file (repeatable)")
    parser.add_argument("--artifact", action="append", default=[], help="declared output artifact (repeatable)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture one foreground execution and check its receipt.")
    sub = parser.add_subparsers(dest="mode", required=True)
    run_parser = sub.add_parser("run", help="execute direct argv once and write a receipt")
    _add_scope_arguments(run_parser)
    run_parser.add_argument("--timeout", type=float, default=30.0)
    run_parser.add_argument("command", nargs=argparse.REMAINDER, help="command argv after --")
    check_parser = sub.add_parser("check", help="read-only receipt and workspace check")
    _add_scope_arguments(check_parser)
    args = parser.parse_args(argv)
    try:
        if args.mode == "run":
            command = args.command
            if command and command[0] == "--":
                command = command[1:]
            result = run_command(
                args.workspace, args.receipt, args.input, args.artifact, command, args.timeout
            )
        else:
            result = check_receipt(args.workspace, args.receipt, args.input, args.artifact)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        if result.get("receipt_valid") is False:
            return 2
        return 0
    except (ReceiptError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
