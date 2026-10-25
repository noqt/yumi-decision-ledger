"""Focused synthetic-package tests for the read-only consistency checker."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from verify_package import verify_package


REQUIRED = (
    ".gitattributes",
    "README.md",
    "manifest.json",
    "mcp_receipt_server.py",
    "origin_receipt.py",
    "tests/test_mcp_receipt_server.py",
    "tests/test_verify_package.py",
    "verify_package.py",
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_coherent(root: Path) -> dict[str, Path]:
    """Build a tiny package whose component files are executable sentinels."""

    (root / "tests").mkdir(parents=True)
    marker = root / "import-or-exec-sentinel"
    origin = b"raise RuntimeError('origin sentinel must never execute')\n"
    (root / "origin_receipt.py").write_bytes(origin)
    bridge = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        f"EXPECTED_ORIGIN_SHA256 = \"{_digest(origin)}\"\n"
    ).encode("utf-8")
    (root / "mcp_receipt_server.py").write_bytes(bridge)
    (root / "README.md").write_bytes(b"synthetic package documentation\n")
    (root / "tests/test_mcp_receipt_server.py").write_bytes(b"# synthetic bridge test\n")
    (root / "tests/test_verify_package.py").write_bytes(b"# synthetic verifier test\n")
    verifier = Path(__file__).resolve().parents[1] / "verify_package.py"
    (root / "verify_package.py").write_bytes(verifier.read_bytes())

    paths = sorted(REQUIRED)
    attributes = "".join(f"/{path} -text\n" for path in paths).encode("utf-8")
    (root / ".gitattributes").write_bytes(attributes)
    inventory = {"manifest.json": {}}
    for path in paths:
        if path == "manifest.json":
            continue
        inventory[path] = {"sha256": _digest((root / path).read_bytes())}
    manifest = {"name": "synthetic", "files": inventory}
    (root / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return {
        "root": root,
        "marker": marker,
        "origin": root / "origin_receipt.py",
        "bridge": root / "mcp_receipt_server.py",
    }


def _codes(result: dict[str, object]) -> set[str]:
    diagnostics = result.get("diagnostics", [])
    return {
        item["code"]
        for item in diagnostics  # type: ignore[union-attr]
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }


class VerifyPackageTests(unittest.TestCase):
    def package(self) -> tuple[tempfile.TemporaryDirectory[str], dict[str, Path]]:
        temporary = tempfile.TemporaryDirectory()
        return temporary, _write_coherent(Path(temporary.name))

    def test_coherent_success_and_no_component_execution(self) -> None:
        temporary, package = self.package()
        try:
            result = verify_package(package["root"])
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["exit_code"], 0)
            self.assertFalse(package["marker"].exists())
        finally:
            temporary.cleanup()

    def test_external_manifest_self_digest_may_be_omitted(self) -> None:
        temporary, package = self.package()
        try:
            manifest = json.loads((package["root"] / "manifest.json").read_text())
            self.assertNotIn("sha256", manifest["files"]["manifest.json"])
            self.assertEqual(verify_package(package["root"])["status"], "PASS")
        finally:
            temporary.cleanup()

    def test_attributes_missing_duplicate_glob_and_wrong_rule_refuse(self) -> None:
        cases = {
            "missing": lambda lines: lines[:-1],
            "duplicate": lambda lines: lines + [lines[0]],
            "glob": lambda lines: ["/*README.md -text" if "README.md" in line else line for line in lines],
            "wrong": lambda lines: [line.replace(" -text", " text") if "README.md" in line else line for line in lines],
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                temporary, package = self.package()
                try:
                    lines = (package["root"] / ".gitattributes").read_text().splitlines()
                    (package["root"] / ".gitattributes").write_text(
                        "\n".join(mutate(lines)) + "\n",
                        encoding="utf-8",
                    )
                    result = verify_package(package["root"])
                    self.assertIn(result["status"], {"FAIL", "REFUSED"})
                finally:
                    temporary.cleanup()

    def test_duplicate_json_keys_refuse(self) -> None:
        temporary, package = self.package()
        try:
            (package["root"] / "manifest.json").write_text(
                '{"files": {}, "files": {}}', encoding="utf-8"
            )
            result = verify_package(package["root"])
            self.assertEqual(result["status"], "REFUSED")
            self.assertIn("manifest_structure_invalid", _codes(result))
        finally:
            temporary.cleanup()

    def test_missing_and_modified_declared_files_refuse_or_fail(self) -> None:
        temporary, package = self.package()
        try:
            package["origin"].unlink()
            result = verify_package(package["root"])
            self.assertEqual(result["status"], "REFUSED")
        finally:
            temporary.cleanup()

        temporary, package = self.package()
        try:
            (package["root"] / "README.md").write_bytes(b"modified raw bytes\n")
            result = verify_package(package["root"])
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("manifest_digest_mismatch", _codes(result))
        finally:
            temporary.cleanup()

    def test_unsafe_traversing_and_special_declared_inputs_refuse(self) -> None:
        temporary, package = self.package()
        try:
            manifest = json.loads((package["root"] / "manifest.json").read_text())
            manifest["files"]["../outside.txt"] = {"sha256": "0" * 64}
            (package["root"] / "manifest.json").write_text(
                json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
            )
            self.assertEqual(verify_package(package["root"])["status"], "REFUSED")
        finally:
            temporary.cleanup()

        temporary, package = self.package()
        try:
            (package["root"] / "special").mkdir()
            manifest = json.loads((package["root"] / "manifest.json").read_text())
            manifest["files"]["special"] = {"sha256": "0" * 64}
            (package["root"] / ".gitattributes").write_text(
                (package["root"] / ".gitattributes").read_text() + "/special -text\n",
                encoding="utf-8",
            )
            (package["root"] / "manifest.json").write_text(
                json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
            )
            self.assertEqual(verify_package(package["root"])["status"], "REFUSED")
        finally:
            temporary.cleanup()

    def test_linked_declared_input_refuses_when_links_are_available(self) -> None:
        temporary, package = self.package()
        try:
            target = package["root"] / "outside-target"
            target.write_bytes(b"not in package\n")
            linked = package["root"] / "linked.txt"
            try:
                os.symlink(target, linked)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links unavailable on this host")
            manifest = json.loads((package["root"] / "manifest.json").read_text())
            manifest["files"]["linked.txt"] = {"sha256": _digest(target.read_bytes())}
            (package["root"] / ".gitattributes").write_text(
                (package["root"] / ".gitattributes").read_text() + "/linked.txt -text\n",
                encoding="utf-8",
            )
            (package["root"] / "manifest.json").write_text(
                json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
            )
            self.assertEqual(verify_package(package["root"])["status"], "REFUSED")
        finally:
            temporary.cleanup()

    def test_origin_pin_stale_malformed_computed_and_duplicate_refuse_or_fail(self) -> None:
        cases = {
            "stale": 'EXPECTED_ORIGIN_SHA256 = "' + ("0" * 64) + '"\n',
            "malformed": 'EXPECTED_ORIGIN_SHA256 = "BAD"\n',
            "computed": 'EXPECTED_ORIGIN_SHA256 = hashlib.sha256(b"x").hexdigest()\n',
            "duplicate": 'EXPECTED_ORIGIN_SHA256 = "' + ("0" * 64) + '"\nEXPECTED_ORIGIN_SHA256 = "' + ("1" * 64) + '"\n',
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                temporary, package = self.package()
                try:
                    package["bridge"].write_text(source, encoding="utf-8")
                    result = verify_package(package["root"])
                    if name == "stale":
                        self.assertIn("origin_pin_mismatch", _codes(result))
                    else:
                        self.assertTrue(
                            {"bridge_pin_malformed", "bridge_pin_not_literal", "bridge_pin_shape"}
                            & _codes(result)
                        )
                finally:
                    temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
