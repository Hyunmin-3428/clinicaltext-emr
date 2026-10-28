"""Private bounded pipe runner for an already installed local hybrid detector."""
import base64
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    try:
        from ClinicalTextDeid.api import transform
        packet = json.loads(sys.stdin.buffer.read(70001))
        result = transform(packet["request"], key=base64.b64decode(packet["key"], validate=True))
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
        return 0
    except Exception:
        return 2


if __name__ == "__main__":
    sys.exit(main())
