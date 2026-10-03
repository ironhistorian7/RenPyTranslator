"""Shared explicit task selection for GUI and CLI."""
TASKS = {
    'run': '번역', 'retranslate': '재번역', 'font': '폰트·메뉴 보정', 'names': '이름·호칭·입력 보정',
    'failed': '미번역 복구', 'display': '원문 참고 표시 보정',
    'layout': '대화창·선택지 표시 조정', 'answers': '입력 정답 표시', 'routes': '선택지 힌트',
    'language': '언어 전환 패널',
}
FIXES = {'font', 'names', 'failed', 'display', 'layout'}


def plan(selected):
    selected = set(selected)
    if not selected: raise ValueError('실행할 작업을 하나 이상 선택하세요.')
    if selected - TASKS.keys(): raise ValueError('알 수 없는 작업입니다.')
    if {'run','retranslate'} <= selected: raise ValueError('번역과 재번역 중 하나만 선택하세요.')
    if selected & {'run','retranslate'}: selected -= FIXES | {'language'}
    if 'font' in selected: selected -= {'display', 'layout'}
    if 'display' in selected: selected.discard('layout')
    # Correct cached text first, then install the selected display support.
    return [key for key in ('run','retranslate','failed','names','font','display','layout','answers','routes','language') if key in selected]


def arguments(selected, path, source=False, output='', suffix='-kr', scale='default', corner=None, margin=None, source_language=None):
    jobs = plan(selected)
    if not path.strip(): raise ValueError('게임 또는 기존 프로젝트 폴더를 선택하세요.')
    if source and any(job in FIXES | {'language'} for job in jobs):
        raise ValueError('보정만 실행할 때는 기존 프로젝트 또는 결과 폴더를 선택하세요.')
    args = ['tasks', '--tasks', *jobs, '--source' if source else '--project', path]
    if output.strip(): args += ['--output', output.strip()]
    if source_language is not None:
        from source_language import normalize
        args += ['--source-language',normalize(source_language)]
    if corner is not None:args += ['--language-corner',corner]
    if margin is not None:
        if not 0<=int(margin)<=300:raise ValueError('언어 패널 여백은 0~300입니다.')
        args += ['--language-margin',str(int(margin))]
    return args + ['--suffix='+suffix, '--textbox-scale', scale.strip() or 'default']
