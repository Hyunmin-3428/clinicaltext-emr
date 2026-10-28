"""CLI subprocess isolation and temporary synthetic byte-exact integration tests."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ClinicalTextDeid.evaluate import CONTEXT

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "ClinicalTextDeid.cli", *map(str, args)],
                              cwd=ROOT.parent, capture_output=True, text=True, encoding="utf-8")

    def setup_files(self, base, private=None):
        private = private or base / "internal"
        private.mkdir(parents=True, exist_ok=True)
        raw = "\ufeffName: John Smith.\r\n\t심근경색 🙂  \naspirin 81 mg.\n".encode("utf-8")
        request = {"utf8_b64": base64.b64encode(raw).decode(), "context": CONTEXT, "detector": "rules"}
        source, key, context = base / "input.json", private / "key.bin", private / "context.json"
        source.write_text(json.dumps(request), encoding="utf-8-sig")
        key.write_bytes(os.urandom(32))
        context.write_text(json.dumps(CONTEXT), encoding="utf-8")
        return raw, source, key, context

    def test_fresh_process_transform_restore_byte_exact(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            base = Path(tmp)
            raw, source, key, context = self.setup_files(base)
            public, internal = base / "public" / "payload.json", base / "internal" / "result.json"
            result = self.cli("transform", "--input", source, "--public-out", public, "--internal-out", internal, "--key-file", key)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "created")
            exported = json.loads(public.read_bytes())
            self.assertEqual(set(exported), {"schema_version", "text", "validation_status"})
            self.assertNotIn("John Smith", exported["text"])
            for value in ["John Smith", "ciphertext", "capsule", "nonce"]:
                self.assertNotIn(value, result.stdout + result.stderr)
            output = base / "internal" / "restored.txt"
            restored = self.cli("restore", "--input", internal, "--output", output, "--key-file", key, "--context-file", context)
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual(output.read_bytes(), raw)
            again = self.cli("restore", "--input", internal, "--output", output, "--key-file", key, "--context-file", context)
            self.assertEqual(again.returncode, 2)
            self.assertEqual(output.read_bytes(), raw)
            # No private artifacts survive TemporaryDirectory cleanup.

    def test_nested_output_directories_rejected_both_directions(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            base = Path(tmp)
            for i, (public_dir, private_dir) in enumerate([(base / "one", base / "one" / "inside"), (base / "two" / "inside", base / "two")]):
                raw, source, key, context = self.setup_files(base, private_dir)
                public, internal = public_dir / "payload.json", private_dir / "result.json"
                result = self.cli("transform", "--input", source, "--public-out", public, "--internal-out", internal, "--key-file", key)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(public.exists())
                self.assertFalse(internal.exists())

    def test_path_alias_to_same_directory_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            base = Path(tmp)
            raw, source, key, context = self.setup_files(base)
            private = key.parent
            public = private / ".." / "internal" / "payload.json"
            internal = private / "result.json"
            result = self.cli("transform", "--input", source, "--public-out", public, "--internal-out", internal, "--key-file", key)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(public.exists())
            self.assertFalse(internal.exists())

    def test_symlink_alias_rejected_where_supported(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            base = Path(tmp)
            raw, source, key, context = self.setup_files(base)
            alias = base / "public-alias"
            try:
                alias.symlink_to(key.parent, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Directory symlink unavailable: {type(exc).__name__}")
            result = self.cli("transform", "--input", source, "--public-out", alias / "payload.json", "--internal-out", key.parent / "result.json", "--key-file", key)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((key.parent / "payload.json").exists())
            self.assertFalse((key.parent / "result.json").exists())

    def test_restore_public_destination_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            base = Path(tmp)
            raw, source, key, context = self.setup_files(base)
            public, internal = base / "public" / "payload.json", base / "internal" / "result.json"
            transformed = self.cli("transform", "--input", source, "--public-out", public, "--internal-out", internal, "--key-file", key)
            self.assertEqual(transformed.returncode, 0)
            output = base / "public" / "plaintext.txt"
            restored = self.cli("restore", "--input", internal, "--output", output, "--key-file", key, "--context-file", context)
            self.assertEqual(restored.returncode, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
