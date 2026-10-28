"""Package source and synthetic fixtures, including every pinned runtime file."""
import hashlib
import json
import os
from pathlib import Path
import zipfile
from .temporal_policy import POLICY_VERSION


def build():
    base=Path(__file__).resolve().parent.parent
    out=base/"ClinicalTextWeb/.runtime/server-source-current.zip"
    out.parent.mkdir(exist_ok=True)
    excluded={".venv",".runtime",".git","__pycache__","docs","research","evaluation","node_modules"}
    allowed={".py",".json",".txt",".toml",".yaml",".yml",".html",".css",".js",".md",".sh"}
    paths=set()
    for name in ["ClinicalTextWeb","ClinicalTextDeid","ClinicalCDSGateway","ClinicalTextIntegration"]:
        for directory,children,files in os.walk(base/name):
            children[:]=[d for d in children if d not in excluded]
            for filename in files:
                p=Path(directory)/filename
                if p.suffix.lower() in allowed or p.name in {"LICENSE","NOTICE"}:paths.add(p.relative_to(base))
    for prefix,manifest_name in [("ClinicalTextDeid/vendor/ko-pii","MANIFEST.json"),("ClinicalTextWeb/vendor/local-llm-deid","../local-llm-deid.manifest.json")]:
        manifest=json.loads((base/prefix/manifest_name).read_text(encoding="utf8"))
        for entry in manifest["files"]:
            p=base/prefix/entry["path"]
            assert p.resolve().is_relative_to((base/prefix).resolve())
            assert hashlib.sha256(p.read_bytes()).hexdigest()==entry["sha256"]
            paths.add(p.relative_to(base))
    entries=[]
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(paths):
            data=(base/rel).read_bytes()
            if rel.suffix==".sh":data=data.replace(b"\r\n",b"\n")
            z.writestr(rel.as_posix(),data)
            entries.append({"path":rel.as_posix(),"sha256":hashlib.sha256(data).hexdigest()})
        z.writestr("SOURCE_MANIFEST.json",json.dumps({"policy_version":POLICY_VERSION,"synthetic_only":True,"files":entries},indent=2))
    result={"file":str(out),"bytes":out.stat().st_size,"sha256":hashlib.sha256(out.read_bytes()).hexdigest(),"files":len(entries)}
    print(json.dumps(result,ensure_ascii=True))
    return result


if __name__=="__main__":build()
