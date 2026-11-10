"""Public operation stages, separate from per-request diagnostic context."""
from job_events import set_phase


PUBLIC_STAGES={
    'inspect and prepare':('prepare','작업 파일 준비 중'),
    'RPG Maker prepare':('prepare','작업 자료 확인 중'),
    'name preparation':('names','인명 표기 정리 중'),
    'name correction':('names','인명 표기 보정 중'),
    'translation':('translation','번역 준비 중'),
    'RPG Maker translation':('translation','번역 준비 중'),
    'failed item repair':('recovery','미번역 항목 복구 중'),
    'repair':('repair','기존 번역 보정 중'),
    'presentation':('presentation','표시 설정 준비 중'),
    'render':('render','번역 파일 생성 중'),
    'RPG Maker patch output':('render','번역 파일 생성 중'),
    'install support':('support','글꼴·표시 설정 적용 중'),
    'validate':('validate','패치 검사 중'),
    'patch check':('validate','패치 검사 중'),
    'package':('package','결과 저장 중'),
    'in-game hints':('guides','정답·선택지 힌트 처리 중'),
}


def public_stage(name,project=None,cfg=None):
    value=PUBLIC_STAGES.get(name)
    if value:set_phase(*value)
