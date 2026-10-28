"""Build the public queue catalog from the same authored records as the agent."""
from pathlib import Path
import json
from ClinicalTextWeb.service import examples
from demo_server import PURPOSES, RECORD_INFO


def catalog():
    records = [{**r, **dict(zip(["category","department","date","title"], RECORD_INFO[r["id"]]))}
               for r in examples()]
    return {"model":"qwen3:30b", "records":records, "purposes":PURPOSES}


if __name__ == "__main__":
    path = Path(__file__).resolve().parent / "catalog.json"
    path.write_text(json.dumps(catalog(),ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("Public synthetic catalog synchronized.")
