"""Prepare a static GitHub Pages frontend; GPU processing stays on the server."""
from pathlib import Path
import json
import shutil

root=Path(__file__).resolve().parent
docs=root/'docs';docs.mkdir(exist_ok=True)
for name in ['index.html','styles.css','app.js','favicon.svg','qr.svg','qr.png']:
    shutil.copy2(root/'dist'/name,docs/name)
config={'apiBase':'https://clinicaltext-emr.alexchoi676.chatgpt.site','publicUrl':'https://hyunmin-3428.github.io/clinicaltext-emr/','githubUrl':'https://github.com/Hyunmin-3428/clinicaltext-emr'}
(docs/'config.js').write_text('window.CLINICAL_DEMO = '+json.dumps(config)+';\n',encoding='utf8')
(docs/'.nojekyll').write_text('',encoding='utf8')
print('GitHub Pages frontend prepared; no server credentials included.')
