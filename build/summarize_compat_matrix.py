"""Summarize only this run's synthetic compatibility artifacts."""
from pathlib import Path
import hashlib
import json

root=Path(__file__).resolve().parent.parent
out=root/'build/renpy-validation/compat-20260922'
matrix=json.loads((out/'matrix.json').read_text(encoding='utf-8'))
render={r['version']:r for r in json.loads((out/'render-matrix.json').read_text(encoding='utf-8'))}
hashes=json.loads((out/'source-hashes.json').read_text(encoding='utf-8'))
current={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'app').glob('*') if p.is_file()}
assert current==hashes
assert len(matrix)==12 and len(render)==12
assert all(r['passed'] and len(r['layouts'])==3 for r in render.values())
lines=['# RenPyTranslator 실제 엔진 호환성 시험 — 2026-09-22','',
'**결론: 지원 목표 충족 실패. 제품 코드의 결함 2종을 실제 SDK에서 재현했다. 제품 코드는 수정하지 않았다.**','',
'## 범위와 방법','',
'- 엔진: 아래 공식 SDK 12개. 7.3~7.8, 8.0~8.5 각 계열의 대표 버전이며 모든 중간 패치 릴리스의 전수검증은 아니다.',
'- [공식 릴리스 목록](https://www.renpy.org/release_list.html). 이번에 추가 받은 SDK의 URL과 SHA-256은 `../compat-downloads.json`에 기록했다.',
'- 사용자 게임, 기존 번역 프로젝트, 번역 문장, 로컬 모델은 사용하지 않았다. build/renpy-validation 아래 새로 만든 짧은 합성 샘플만 사용했다.',
'- 현재 제품의 설치/패치 생성 함수를 호출하여 실제 .rpy를 생성하고, 해당 SDK의 Python과 Ren\'Py로 컴파일 및 초기화했다.',
'- Python 2 최적화 옵션에서도 검사가 생략되지 않도록 assert를 명시적 조건 검사와 예외 발생으로 바꾼 테스트 하네스를 사용했다. 제품 코드를 바꾼 것은 아니다.',
'- API, 이름 치환, 입력창: 실제 엔진 함수와 Screen/Input 객체 사용. ui.interact의 대기 부분만 자동 응답으로 대체했다. 물리 키보드 입력·IME·세이브/롤백 전체 동작은 시험하지 않았다.',
'- 선택지: 별도 숨김 Windows 데스크톱에서 실제 렌더러로 1280×720/2개, 1920×1080/6개, 800×600/12개를 시험했다. 긴 힌트, 화면 경계, 패널과 선택지 영역 분리, 마지막 항목 포커스와 스크롤, 선택값 보존을 검사했다. 화면 캡처가 단색이 아닌지도 검사했다.',
'- 소스 무변경: app 직속 파일의 시험 전후 SHA-256 일치. `source-hashes.json` 참조.','',
'## 버전별 결과','',
'| 실제 엔진 | 공통 표시/API/입력 기본 경로 | 함수형 이름·영문 참고·조사 치환 | 위치 인수 입력 | 선택지 실제 렌더 |',
'|---|---|---|---|---|']
for row in matrix:
    checks={c['name']:c for c in row['checks']}
    version=row['version'].split()[1]
    short='.'.join(version.split('.')[:3])
    common=['font_reference_size_josa_layout','real_translation_api_cached_and_fallback','input_default_real_screen','input_custom_value_real_screen','answer_prompt_real_screen']
    functions=['name_interpolation_and_english_reference','english_reference_interpolation','particle_interpolation']
    common_ok=all(checks[n]['status']=='pass' for n in common)
    functions_ok=all(checks[n]['status']=='pass' for n in functions)
    lines.append('| '+row['version']+' | '+('통과' if common_ok else '실패')+' | '+('통과' if functions_ok else '실패 A')+' | 실패 B | 3/3 통과 |')
lines += ['',
'공통 표시 검사는 실제 폰트 분할/한글 글리프 생성, 대사·중앙 텍스트·선택지의 55% 영문 크기(기존/현재 태그 및 잘못된 크기 보정), 대화창 높이 복원, 캐시 선택지와 번역 API fallback, 기본 별명 표시 및 원래 반환값 보존, 사용자 지정 이름 보존, 정답 프롬프트 생성을 포함한다. 조사 함수 자체의 검사와 문장 안의 조사 치환 검사는 별도이며, 후자는 아래 A로 실패한다.','',
'## 실패 A — 구형 엔진에 지원되지 않는 함수형 보간 생성','',
'- 재현: 7.3.5, 7.4.11, 7.5.3, 7.6.0, 8.0.3, 8.1.0.',
'- 정상 단순 변수 `[nickname!q]`는 통과하지만 `[rpt_display_name(nickname)!q]`, `[rpt_reference_name(nickname)!q]`, `[rpt_josa(nickname, u\'은/는\')]`는 실패한다.',
'- 구형 보간기는 함수 호출 문자열을 계산하지 않고 변수 키처럼 처리한다. 시험에서는 구형 Python 2 엔진이 치환 전 문자열을 남겼고, 8.0/8.1에서는 KeyError가 발생했다.',
'- 같은 코드가 7.7.3/7.8.7/8.2.3/8.3.7/8.4.1/8.5.3에서는 통과했다. 이 시험만으로 모든 중간 릴리스의 도입 경계까지 확정하지는 않는다.',
'- 제품 원인 위치: `app/display_text.py` 22, 59, 82행. 엔진 버전에 대한 분기 없이 함수형 보간을 생성한다.',
'- 수정 필요 방향: 구형에서도 지원되는 표시 치환 방식으로 이름/영문 참고/조사를 함께 처리해야 한다. 아직 수정하지 않았다.','',
'## 실패 B — renpy.input 위치 인수 순서 오해','',
'- 12개 버전 모두 `renpy.input("Nickname?", "Bear", None, None, 20)` 경로에서 TypeError를 재현했다.',
'- 실제 순서는 prompt 뒤에 default, allow, exclude, length이다.',
'- 제품 `app/names_runtime.py` 81~83행은 default, length, allow, exclude 순서로 읽는다. 길이 숫자를 제외 문자 집합으로 취급하여 int is not iterable 오류가 난다.',
'- ASCII allow 문자열을 위치 인수로 넘기는 추가 사례에서는 모든 8.x 시험에서 int와 str 비교 오류도 발생했다. Python 2에서는 이 사례만 우연히 통과하므로 정상 지원의 근거가 될 수 없다.',
'- 키워드 인수(default=...)의 기본 입력 경로, 기본 별명 한국어 표시, 기본값 수락 시 원래 값 반환, 다른 입력 그대로 반환은 통과했다.',
'- 수정 필요 방향: 실제 엔진 입력 함수의 인수 순서와 일치시켜야 한다. 아직 수정하지 않았다.','',
'## 산출물과 재현','',
'- `matrix.json`: 실제 엔진의 기능 검사 120개 결과. 각 실패에 전체 traceback 포함.',
'- `render-matrix.json`: 12개 엔진 × 3개 화면 구성 = 36개 렌더 시험.',
'- `<버전>/report.json`, `validation-output.txt`, `render-output.txt`: 버전별 결과와 로그.',
'- `<버전>/staging/layout-*.png`: 실제 선택지 렌더 이미지.',
'- 테스트 하네스: `build/verify_compat_matrix.py`, `build/verify_compat_render.py`.',
'- 테스트 하네스 구축 중의 init/rollback 준비, SDL dummy 제한, 캡처 API 차이는 하네스에서 해결했다. 위 표는 최종 재실행 결과이며 이 시험 환경 문제를 제품 결함으로 집계하지 않았다.',
'- 전체 번역·모델 추론·실제 게임 실행·사용자 게임 파일 수정·제품 코드 수정·포터블 재패키징은 하지 않았다.','']
(out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
print('Report:',out/'REPORT.md')
print('Runtime checks:',sum(len(r['checks']) for r in matrix))
print('Runtime passed:',sum(c['status']=='pass' for r in matrix for c in r['checks']))
print('Render cases:',sum(len(r['layouts']) for r in render.values()))
print('Product hashes unchanged:',len(hashes))
