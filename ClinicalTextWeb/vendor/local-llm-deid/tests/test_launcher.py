"""Test launcher arguments with fake tools; no model or network is used."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("bash"), "bash is required")
class LauncherTest(unittest.TestCase):
    def test_help_and_missing_values(self):
        script = ROOT / "scripts/run_local_end_to_end.sh"
        help_result = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--out-dir", help_result.stdout)
        self.assertIn("--overwrite-model-output", help_result.stdout)
        result = subprocess.run(["bash", str(script), "--gguf"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing value", result.stderr)

    def test_hf_download_and_launch_provenance_are_forwarded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            script = root / "scripts/run_local_end_to_end.sh"
            shutil.copyfile(ROOT / "scripts/run_local_end_to_end.sh", script)
            tools = root / "bin"
            tools.mkdir()
            log = root / "calls.jsonl"
            ready = root / "ready"
            header = "#!" + sys.executable + "\n"
            programs = {
                "hf": """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:3] == ['download', 'test/model', 'weights.gguf']
dest = Path(args[args.index('--local-dir') + 1])
dest.mkdir(parents=True, exist_ok=True)
(dest / 'weights.gguf').write_bytes(b'synthetic checkpoint')
""",
                "curl": """import os, sys
from pathlib import Path
sys.exit(0 if Path(os.environ['TEST_READY']).exists() else 1)
""",
                "llama-server": """import os, signal, sys, time
from pathlib import Path
if sys.argv[1:] == ['--version']:
    print('synthetic-test-build')
    sys.exit(0)
ready = Path(os.environ['TEST_READY'])
ready.touch()
def stop(*args):
    ready.unlink(missing_ok=True)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
while True:
    time.sleep(0.05)
""",
                "python3": """import json, os, sys
with open(os.environ['TEST_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
""",
            }
            for name, program in programs.items():
                path = tools / name
                path.write_text(header + program)
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ.get("PATH", ""),
                       TEST_READY=str(ready), TEST_LOG=str(log))
            result = subprocess.run([
                "bash", str(script), "--hf-repo", "test/model", "--hf-file", "weights.gguf",
                "--model-id", "test-model", "--out-dir", str(root / "out"),
            ], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            runner = next(args for args in calls if args[:2] == ["-m", "deid.run_model"])
            self.assertIn("--checkpoint-file", runner)
            self.assertIn("--server-version=synthetic-test-build", runner)
            command = runner[runner.index("--server-command") + 1:]
            self.assertEqual(command[:2], ["llama-server", "-m"])
            self.assertEqual(command[-1], "--jinja")
            self.assertEqual(command[2], str(root / "models/model/weights.gguf"))
            self.assertFalse(ready.exists())  # cleanup stopped the fake server


if __name__ == "__main__":
    unittest.main()
