#!/usr/bin/env python3
"""Bounded, read-only consistency check for this adjacent package.

This module deliberately does not import any package component.  It reads only
the explicitly selected package directory, its manifest and attributes file,
and the files named by that manifest.  The result is a local consistency
diagnostic; it is not an authenticity, signature, safety, sandbox, or
atomic-snapshot mechanism.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import stat
import sys
import unicodedata
from typing import Any, Mapping, Sequence


MANIFEST_NAME = "manifest.json"
ATTRIBUTES_NAME = ".gitattributes"
ORIGIN_NAME = "origin_receipt.py"
BRIDGE_NAME = "mcp_receipt_server.py"
PIN_NAME = "EXPECTED_ORIGIN_SHA256"

MAX_MANIFEST_BYTES = 128 * 1024
MAX_ATTRIBUTES_BYTES = 16 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_INVENTORY_FILES = 64
MAX_AGGREGATE_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024
MAX_DIAGNOSTICS = 64
MAX_DIAGNOSTIC_PATH = 120

REQUIRED_FILES = frozenset(
    {
        MANIFEST_NAME,
        ATTRIBUTES_NAME,
        "verify_package.py",
        "tests/test_verify_package.py",
        "README.md",
        BRIDGE_NAME,
        ORIGIN_NAME,
        "tests/test_mcp_receipt_server.py",
    }
)

_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)


class _DuplicateJSONKey(ValueError):
    pass


class _ManifestRefusal(ValueError):
    pass


class _DiagnosticCollector:
    """Keep diagnostics small and free of source bytes or exception text."""

    __slots__ = ("items", "structural", "consistency", "truncated")

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []
        self.structural = False
        self.consistency = False
        self.truncated = False

    def add(
        self,
        code: str,
        *,
        path: str | None = None,
        kind: str = "structural",
    ) -> None:
        if kind == "consistency":
            self.consistency = True
        else:
            self.structural = True
        if len(self.items) >= MAX_DIAGNOSTICS:
            self.truncated = True
            return
        item: dict[str, str] = {"code": code}
        if path is not None:
            item["path"] = _display_path(path)
        self.items.append(item)


def _display_path(value: str) -> str:
    """Return a bounded, control-free path label for diagnostics."""

    clean = "".join("?" if ord(ch) < 32 or ord(ch) == 127 else ch for ch in value)
    if len(clean) > MAX_DIAGNOSTIC_PATH:
        return clean[: MAX_DIAGNOSTIC_PATH - 1] + "…"
    return clean


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"unsupported JSON constant: {value}")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result


def _parse_manifest(raw: bytes) -> dict[str, dict[str, Any]]:
    try:
        text = raw.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except _DuplicateJSONKey as exc:
        raise _ManifestRefusal("duplicate JSON key") from exc
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        RecursionError,
    ) as exc:
        raise _ManifestRefusal("manifest JSON is not strict UTF-8 JSON") from exc

    if not isinstance(parsed, dict):
        raise _ManifestRefusal("manifest root is not an object")
    inventory = parsed.get("files")
    if not isinstance(inventory, dict):
        raise _ManifestRefusal("manifest files inventory is not an object")
    if not inventory or len(inventory) > MAX_INVENTORY_FILES:
        raise _ManifestRefusal("manifest inventory count is outside its bound")

    checked: dict[str, dict[str, Any]] = {}
    folded: dict[str, str] = {}
    for path, descriptor in inventory.items():
        if not isinstance(path, str):
            raise _ManifestRefusal("manifest inventory key is not a string")
        _validate_relative_path(path)
        fold = _path_alias(path)
        previous = folded.get(fold)
        if previous is not None:
            # Do not silently case-fold or otherwise collapse Windows aliases.
            raise _ManifestRefusal("manifest inventory contains a path alias")
        folded[fold] = path
        if not isinstance(descriptor, dict):
            raise _ManifestRefusal("manifest file descriptor is not an object")
        if any(key not in {"role", "sha256"} for key in descriptor):
            raise _ManifestRefusal("manifest file descriptor has an unsupported key")
        role = descriptor.get("role")
        if "role" in descriptor and (not isinstance(role, str) or len(role) > 4096):
            raise _ManifestRefusal("manifest file role has the wrong type")
        if "sha256" not in descriptor and path != MANIFEST_NAME:
            raise _ManifestRefusal("only manifest.json may omit sha256")
        digest = descriptor.get("sha256")
        if "sha256" in descriptor and not _is_digest(digest):
            raise _ManifestRefusal("manifest sha256 has the wrong type or form")
        checked[path] = descriptor

    if MANIFEST_NAME not in checked:
        raise _ManifestRefusal("manifest inventory omits manifest.json")
    return checked


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _path_alias(path: str) -> str:
    # NFC catches the common Windows normalization alias while preserving the
    # original spelling for the actual file access and exact-set comparison.
    return unicodedata.normalize("NFC", path).casefold()


def _validate_relative_path(path: str) -> tuple[str, ...]:
    """Validate a manifest/attributes path without resolving or discovering it."""

    if not path or len(path) > 1024:
        raise _ManifestRefusal("path is empty or too long")
    if path.startswith(("/", "\\")) or "\\" in path or ":" in path:
        raise _ManifestRefusal("path is absolute or uses an unsafe Windows separator")
    pieces = path.split("/")
    if any(not piece or piece in {".", ".."} for piece in pieces):
        raise _ManifestRefusal("path contains traversal or an empty component")
    for piece in pieces:
        if any(
            ord(char) < 32
            or ord(char) == 127
            or 0xD800 <= ord(char) <= 0xDFFF
            for char in piece
        ):
            raise _ManifestRefusal("path contains a control character")
        if any(char in piece for char in "*?[]"):
            raise _ManifestRefusal("path contains a glob metacharacter")
        if piece.endswith((".", " ")):
            raise _ManifestRefusal("path has a Windows trailing-dot/space alias")
        folded = piece.casefold()
        if folded == ".git" or (folded.startswith(".git") and folded != ".gitattributes"):
            raise _ManifestRefusal("path names Git metadata")
        # Windows reserves these device names even when an extension is added.
        if folded.split(".", 1)[0] in _WINDOWS_RESERVED_NAMES:
            raise _ManifestRefusal("path names a Windows device")
    return tuple(pieces)


def _is_reparse(st: os.stat_result) -> bool:
    # Windows exposes FILE_ATTRIBUTE_REPARSE_POINT here; POSIX has no
    # equivalent, while S_ISLNK below catches symbolic links.
    attributes = getattr(st, "st_file_attributes", 0)
    return bool(attributes & 0x400)


def _identity(st: os.stat_result) -> tuple[Any, ...]:
    return (
        getattr(st, "st_dev", None),
        getattr(st, "st_ino", None),
        getattr(st, "st_mode", None),
        getattr(st, "st_size", None),
        getattr(st, "st_mtime_ns", None),
        getattr(st, "st_ctime_ns", None),
        getattr(st, "st_file_attributes", None),
        getattr(st, "st_reparse_tag", None),
    )


def _same_entry(path_stat: os.stat_result, handle_stat: os.stat_result) -> bool:
    """Match a path lstat to its opened handle without Windows ctime drift."""

    path_device = getattr(path_stat, "st_dev", None)
    handle_device = getattr(handle_stat, "st_dev", None)
    path_inode = getattr(path_stat, "st_ino", None)
    handle_inode = getattr(handle_stat, "st_ino", None)
    if (
        path_device is not None
        and handle_device is not None
        and path_inode not in (None, 0)
        and handle_inode not in (None, 0)
    ):
        return path_device == handle_device and path_inode == handle_inode

    # Some filesystems do not expose a usable file id.  Keep the path/handle
    # comparison conservative with metadata that is not creation-time based;
    # the opened-handle pre/post check below still uses the full identity.
    return (
        getattr(path_stat, "st_mode", None) == getattr(handle_stat, "st_mode", None)
        and getattr(path_stat, "st_size", None) == getattr(handle_stat, "st_size", None)
        and getattr(path_stat, "st_mtime_ns", None)
        == getattr(handle_stat, "st_mtime_ns", None)
        and getattr(path_stat, "st_file_attributes", None)
        == getattr(handle_stat, "st_file_attributes", None)
        and getattr(path_stat, "st_reparse_tag", None)
        == getattr(handle_stat, "st_reparse_tag", None)
    )


def _lstat(path: str) -> os.stat_result:
    return os.stat(path, follow_symlinks=False)


def _root_is_directory(root: str) -> bool:
    try:
        st = _lstat(root)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode) and not _is_reparse(st)


def _candidate_path(root: str, relative: str) -> tuple[str, tuple[str, ...]]:
    pieces = _validate_relative_path(relative)
    root_abs = os.path.abspath(root)
    candidate = os.path.abspath(os.path.join(root_abs, *pieces))
    try:
        if os.path.commonpath((root_abs, candidate)) != root_abs:
            raise _ManifestRefusal("path escapes package root")
    except ValueError as exc:
        raise _ManifestRefusal("path is on a different volume") from exc
    return candidate, pieces


def _parents_are_directories(root: str, pieces: Sequence[str]) -> None:
    current = root
    for piece in pieces[:-1]:
        current = os.path.join(current, piece)
        st = _lstat(current)
        if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode) or _is_reparse(st):
            raise _ManifestRefusal("path parent is not a regular directory")


def _read_bounded_file(
    root: str,
    relative: str,
    *,
    max_bytes: int,
    remaining_aggregate: int,
) -> tuple[bytes | None, str | None]:
    """Read one declared file with no-follow checks and stable fstat bounds."""

    try:
        candidate, pieces = _candidate_path(root, relative)
        _parents_are_directories(os.path.abspath(root), pieces)
        before = _lstat(candidate)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse(before)
        ):
            return None, "not_regular"
        if before.st_size > max_bytes:
            return None, "file_limit"
        if before.st_size > remaining_aggregate:
            return None, "aggregate_limit"

        flags = os.O_RDONLY
        for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flag = getattr(os, flag_name, 0)
            if flag:
                flags |= flag
        try:
            descriptor = os.open(candidate, flags)
        except OSError:
            return None, "open_failed"
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_ISLNK(opened.st_mode)
                or _is_reparse(opened)
            ):
                return None, "not_regular"
            if not _same_entry(before, opened):
                return None, "unstable_file"
            read_limit = min(max_bytes, remaining_aggregate)
            chunks: list[bytes] = []
            total = 0
            while total <= read_limit:
                request = min(64 * 1024, read_limit + 1 - total)
                if request <= 0:
                    break
                try:
                    chunk = os.read(descriptor, request)
                except OSError:
                    return None, "read_failed"
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > read_limit:
                    break
            after = os.fstat(descriptor)
            if _identity(opened) != _identity(after):
                return None, "unstable_file"
            if total > max_bytes:
                return None, "file_limit"
            if total > remaining_aggregate:
                return None, "aggregate_limit"
            return b"".join(chunks), None
        finally:
            os.close(descriptor)
    except _ManifestRefusal:
        return None, "unsafe_path"
    except (OSError, ValueError, TypeError):
        return None, "read_failed"


def _parse_attributes(raw: bytes) -> set[str]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _ManifestRefusal("attributes are not strict UTF-8") from exc

    paths: set[str] = set()
    folded: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        # Keep the grammar intentionally narrow: one root-anchored literal
        # path and exactly the single -text attribute.
        if line.count(" ") != 1 or "\t" in line:
            raise _ManifestRefusal("attributes rule has the wrong shape")
        path_token, attribute = line.split(" ")
        if attribute != "-text" or not path_token.startswith("/"):
            raise _ManifestRefusal("attributes rule is not an explicit -text rule")
        relative = path_token[1:]
        _validate_relative_path(relative)
        if relative in paths:
            raise _ManifestRefusal("attributes rule is duplicated")
        alias = _path_alias(relative)
        if alias in folded:
            raise _ManifestRefusal("attributes rule contains a path alias")
        folded[alias] = relative
        paths.add(relative)
    return paths


def _origin_pin(raw: bytes) -> tuple[str | None, str | None]:
    """Extract the one supported direct module assignment without importing."""

    try:
        source = raw.decode("utf-8", errors="strict")
        tree = ast.parse(source, filename=BRIDGE_NAME, mode="exec")
    except (UnicodeDecodeError, SyntaxError, ValueError, TypeError, RecursionError):
        return None, "bridge_ast_invalid"

    occurrences: list[ast.Name] = []
    deletions: list[ast.Name] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == PIN_NAME:
            if isinstance(node.ctx, ast.Store):
                occurrences.append(node)
            elif isinstance(node.ctx, ast.Del):
                deletions.append(node)
        elif isinstance(node, ast.alias):
            if node.asname == PIN_NAME or (node.asname is None and node.name == PIN_NAME):
                # Import aliases are bindings too, but never the supported
                # literal assignment shape.
                occurrences.append(ast.Name(id=PIN_NAME, ctx=ast.Store()))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == PIN_NAME:
                occurrences.append(ast.Name(id=PIN_NAME, ctx=ast.Store()))
        elif isinstance(node, ast.arg) and node.arg == PIN_NAME:
            occurrences.append(ast.Name(id=PIN_NAME, ctx=ast.Store()))
        elif isinstance(node, ast.ExceptHandler) and node.name == PIN_NAME:
            occurrences.append(ast.Name(id=PIN_NAME, ctx=ast.Store()))
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == PIN_NAME:
            occurrences.append(ast.Name(id=PIN_NAME, ctx=ast.Store()))

    supported: list[ast.Assign] = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == PIN_NAME
        ):
            supported.append(node)

    if len(occurrences) != 1 or deletions or len(supported) != 1:
        return None, "bridge_pin_shape"
    assignment = supported[0]
    value = assignment.value
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return None, "bridge_pin_not_literal"
    if not _is_digest(value.value):
        return None, "bridge_pin_malformed"
    return value.value, None


def _record_read_error(
    diagnostics: _DiagnosticCollector,
    relative: str,
    error: str,
) -> None:
    if error == "aggregate_limit":
        code = "aggregate_limit"
    elif error == "file_limit":
        code = "file_limit"
    elif error == "not_regular":
        code = "declared_file_not_regular"
    elif error == "unsafe_path":
        code = "unsafe_declared_path"
    elif error == "unstable_file":
        code = "declared_file_changed_during_read"
    elif error == "open_failed":
        code = "declared_file_unavailable"
    else:
        code = "declared_file_read_refused"
    diagnostics.add(code, path=relative, kind="structural")


def _result(diagnostics: _DiagnosticCollector) -> dict[str, Any]:
    if diagnostics.structural:
        status, exit_code = "REFUSED", 2
    elif diagnostics.consistency:
        status, exit_code = "FAIL", 1
    else:
        status, exit_code = "PASS", 0
    return {
        "status": status,
        "exit_code": exit_code,
        "diagnostics": diagnostics.items,
        "diagnostics_truncated": diagnostics.truncated,
    }


def _invocation_result(code: str) -> dict[str, Any]:
    diagnostics = _DiagnosticCollector()
    diagnostics.add(code)
    return _result(diagnostics)


def _verify_package_impl(
    package_root: str | os.PathLike[str],
    *,
    enforce_adjacent: bool = False,
) -> dict[str, Any]:
    """Verify one explicitly selected package directory and return bounded JSON data.

    ``enforce_adjacent`` is used by the CLI: the selected directory must be the
    directory containing this verifier.  Keeping it optional makes the small
    checker straightforward to exercise with synthetic package fixtures.
    """

    diagnostics = _DiagnosticCollector()
    try:
        root = os.fspath(package_root)
    except TypeError:
        return _invocation_result("package_path_invalid")
    if not isinstance(root, str) or not root:
        return _invocation_result("package_path_invalid")
    root_abs = os.path.abspath(root)
    if enforce_adjacent:
        script_dir = os.path.abspath(os.path.dirname(__file__))
        if os.path.normcase(root_abs.rstrip("\\/")) != os.path.normcase(
            script_dir.rstrip("\\/")
        ):
            diagnostics.add("package_is_not_adjacent")
            return _result(diagnostics)
    if not _root_is_directory(root_abs):
        diagnostics.add("package_root_not_regular_directory")
        return _result(diagnostics)

    aggregate = 0
    contents: dict[str, bytes] = {}

    manifest_raw, manifest_error = _read_bounded_file(
        root_abs,
        MANIFEST_NAME,
        max_bytes=MAX_MANIFEST_BYTES,
        remaining_aggregate=MAX_AGGREGATE_BYTES,
    )
    if manifest_raw is None:
        _record_read_error(diagnostics, MANIFEST_NAME, manifest_error or "read_failed")
        return _result(diagnostics)
    aggregate += len(manifest_raw)
    contents[MANIFEST_NAME] = manifest_raw
    try:
        inventory = _parse_manifest(manifest_raw)
    except _ManifestRefusal:
        diagnostics.add("manifest_structure_invalid")
        return _result(diagnostics)

    missing_required = sorted(REQUIRED_FILES.difference(inventory))
    for relative in missing_required:
        diagnostics.add("required_inventory_entry_missing", path=relative)

    if ATTRIBUTES_NAME in inventory:
        attributes_raw, attributes_error = _read_bounded_file(
            root_abs,
            ATTRIBUTES_NAME,
            max_bytes=MAX_ATTRIBUTES_BYTES,
            remaining_aggregate=MAX_AGGREGATE_BYTES - aggregate,
        )
        if attributes_raw is None:
            _record_read_error(diagnostics, ATTRIBUTES_NAME, attributes_error or "read_failed")
        else:
            aggregate += len(attributes_raw)
            contents[ATTRIBUTES_NAME] = attributes_raw
            try:
                attribute_paths = _parse_attributes(attributes_raw)
            except _ManifestRefusal:
                diagnostics.add("attributes_structure_invalid", path=ATTRIBUTES_NAME)
            else:
                inventory_paths = set(inventory)
                if attribute_paths != inventory_paths:
                    diagnostics.add(
                        "attributes_inventory_mismatch",
                        path=ATTRIBUTES_NAME,
                        kind="consistency",
                    )

    # Read only the names declared by the manifest.  The two files already
    # read above are reused, so each declared file is bounded and read once.
    for relative in sorted(inventory):
        if relative in contents:
            continue
        descriptor = inventory[relative]
        limit = MAX_FILE_BYTES
        data, error = _read_bounded_file(
            root_abs,
            relative,
            max_bytes=limit,
            remaining_aggregate=MAX_AGGREGATE_BYTES - aggregate,
        )
        if data is None:
            _record_read_error(diagnostics, relative, error or "read_failed")
            continue
        aggregate += len(data)
        contents[relative] = data

        digest = descriptor.get("sha256")
        if digest is None:
            # The parser already restricted this exception to manifest.json.
            continue
        observed = hashlib.sha256(data).hexdigest()
        if observed != digest:
            diagnostics.add("manifest_digest_mismatch", path=relative, kind="consistency")

    # Compare the two files read before inventory iteration as well.  This
    # includes the explicit external-self-digest convention for manifest.json.
    for relative in (MANIFEST_NAME, ATTRIBUTES_NAME):
        data = contents.get(relative)
        descriptor = inventory.get(relative)
        if data is None or descriptor is None:
            continue
        digest = descriptor.get("sha256")
        if digest is not None and hashlib.sha256(data).hexdigest() != digest:
            diagnostics.add("manifest_digest_mismatch", path=relative, kind="consistency")

    bridge_raw = contents.get(BRIDGE_NAME)
    origin_raw = contents.get(ORIGIN_NAME)
    if bridge_raw is not None:
        expected, pin_error = _origin_pin(bridge_raw)
        if pin_error is not None:
            diagnostics.add(pin_error, path=BRIDGE_NAME)
        elif expected is not None and origin_raw is not None:
            observed_origin = hashlib.sha256(origin_raw).hexdigest()
            if expected != observed_origin:
                diagnostics.add("origin_pin_mismatch", path=ORIGIN_NAME, kind="consistency")

    return _result(diagnostics)


def verify_package(
    package_root: str | os.PathLike[str],
    *,
    enforce_adjacent: bool = False,
) -> dict[str, Any]:
    """Run one bounded check and convert unexpected runtime refusal to JSON data."""

    try:
        return _verify_package_impl(package_root, enforce_adjacent=enforce_adjacent)
    except Exception:
        diagnostics = _DiagnosticCollector()
        diagnostics.add("runtime_refusal")
        return _result(diagnostics)


def _bounded_json(result: Mapping[str, Any]) -> str:
    payload: dict[str, Any] = {
        "status": result.get("status"),
        "exit_code": result.get("exit_code"),
        "diagnostics": result.get("diagnostics", []),
    }
    if result.get("diagnostics_truncated"):
        payload["diagnostics_truncated"] = True
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= MAX_OUTPUT_BYTES:
        return encoded

    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, list):
        while diagnostics and len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
            diagnostics.pop()
            encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        payload["diagnostics_truncated"] = True
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        encoded = '{"status":"REFUSED","exit_code":2,"diagnostics":[{"code":"output_bound"}]}'
    return encoded


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 0:
        # The script itself explicitly selects its adjacent package when no
        # argument is supplied; a supplied argument is checked against the
        # same adjacency requirement below.
        result = verify_package(os.path.dirname(__file__), enforce_adjacent=True)
    elif len(arguments) != 1 or not isinstance(arguments[0], str) or not arguments[0]:
        result = _invocation_result("expected_one_explicit_package_directory")
    else:
        result = verify_package(arguments[0], enforce_adjacent=True)
    try:
        sys.stdout.write(_bounded_json(result) + "\n")
    except (OSError, UnicodeError):
        return 2
    return int(result.get("exit_code", 2))


if __name__ == "__main__":
    raise SystemExit(main())
