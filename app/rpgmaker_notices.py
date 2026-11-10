"""Adapt upstream notices to this app's actual features and diagnostics."""
from pathlib import Path

from engine import save_json
from job_events import emit


class Notices:
    def __init__(self):
        self.issues=[];self.details=[]

    def progress(self,text):
        # File-by-file success and upstream prose remain available locally.
        if text:self.details.append(text)

    def notice(self,code,**fields):
        self.issues.append(dict(code=code,**fields))

    def save(self,project):
        path=Path(project)/'data/rpg-extraction-notices.json'
        save_json(path,dict(kind='rpg_extraction',issues=self.issues,details=self.details))
        failed=[row for row in self.issues if row['code']=='rpg_read_failure']
        if failed:
            emit('issue',code='rpg_read_failure',title='일부 작업 자료를 읽지 못했습니다.',
                 detail=f'파일 {len(failed)}개를 제외하고 계속합니다.',state='warning',
                 counts={'files':len(failed)},log_path=str(path))
            print(f'읽지 못한 추가 자료 {len(failed)}개를 제외했습니다. 기록: {path}',flush=True)
        for code,label in (('rpg_js_candidates','번역 범위 밖의 플러그인 문구 후보'),
                           ('rpg_identifiers_preserved','게임 동작을 위해 원문으로 유지한 항목')):
            count=sum(row.get('count',0) for row in self.issues if row['code']==code)
            if count:emit('notice',code=code,count=count,text=f'{label}: {count}개')
        return path
