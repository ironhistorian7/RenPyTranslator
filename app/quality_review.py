"""Create an explicit review queue. Warnings are not proof of translation errors."""
import json
from pathlib import Path
import re
import sys
from translation import read_catalog, cache, TOKENS
from engine import save_json

project=Path(sys.argv[1]).resolve()
cfg=json.loads((project/'project.json').read_text(encoding='utf-8'))
rows=read_catalog(project);translated=cache(project,cfg)
checks_path=project/'data/terminology-checks.json'
checks=json.loads(checks_path.read_text(encoding='utf-8')) if checks_path.exists() else {}
accepted_path=project/'data/accepted-quality-warnings.json'
accepted=json.loads(accepted_path.read_text(encoding='utf-8')) if accepted_path.exists() else {}
warnings=[]
for row in rows:
    if row['id'] not in translated:continue
    text=translated[row['id']]['text']; reasons=[]
    for source_term,target_term in checks.items():
        if re.search(r'\b'+re.escape(source_term),row['source'],re.IGNORECASE) and target_term not in text:
            reasons.append(f'Term {source_term} expected {target_term}')
    if row['kind']=='dialogue' and re.search(r'[\u3040-\u30ff\u3400-\u9fff]',text):reasons.append('Japanese/CJK characters remain; review names or intentional originals')
    if row['kind']=='dialogue' and translated[row['id']]['model']!='reviewed override' and re.search(r'(?:습니다|입니다|세요)[.!?]?',text):reasons.append('Formal politeness differs from narration style')
    if reasons and row['source'] not in accepted:warnings.append(dict(row,translation=text,reasons=reasons))
save_json(project/'data/quality-review.json',warnings)
print(f'{len(warnings)} entries flagged for terminology/style review; {len(translated)}/{len(rows)} entries available')
