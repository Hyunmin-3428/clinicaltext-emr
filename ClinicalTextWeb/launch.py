"""Open an existing local demo, or start it once if it is not running."""
import argparse
import json
from urllib.request import urlopen
import webbrowser

from .server import main as serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535")
    from .gpu_bridge import ensure
    try:
        ensure()
    except (RuntimeError, OSError, ValueError, TimeoutError) as error:
        # Keep the UI and saved evaluation readable during a server outage.
        # The configured GPU endpoint remains unchanged; analysis fails closed.
        print(str(error))
        print("웹과 저장된 비교 결과를 엽니다. 새 LLM 처리는 GPU 연결 복구 후 사용할 수 있습니다.")
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urlopen(url + "/api/health", timeout=1) as response:
            healthy = json.loads(response.read(1000))
        if healthy.get("application") == "clinical-text-web":
            webbrowser.open(url)
            return
    except (OSError, ValueError):
        pass
    serve(["--port", str(args.port), "--open"])


if __name__ == "__main__":
    main()
