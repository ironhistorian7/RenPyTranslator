# 게임 업데이트와 패치 검사 / Game updates and patch checks

## 한국어

### 게임 업데이트

기존 프로젝트·결과 폴더를 선택하고 **기타 설정 → 게임 업데이트**를 체크합니다. 이어서 **업데이트된 게임 폴더**에 새 버전을 지정합니다.

새 버전의 스크립트를 준비한 뒤 원문·화자·용도·장면 범위와 주변 항목을 비교합니다. 중간에 대사가 추가되어 위치가 달라졌어도 일치하는 번역은 재사용합니다. 추가·변경됐거나 대응이 모호한 항목은 번역 대상으로 남깁니다. 이전 프로젝트와 결과는 보존하며, 새 결과는 별도 폴더에 저장합니다.

중단한 업데이트는 같은 두 폴더로 다시 실행하면 이어집니다. 일반 **번역**의 재개는 저장된 작업 공간을 사용합니다. 새 게임 파일을 반영할 때는 **게임 업데이트**를 사용하세요.

```powershell
$tool = 'E:\Tools\RenPyTranslator'
& "$tool\translate.ps1" -Command update `
  -Project "$tool\project\YourGame-kr" `
  -Source 'D:\Games\YourGame-new'
```

내부 프로젝트의 `data/update-report.json`에 재사용·추가/변경·모호·삭제 항목 수와 이관 근거를 기록합니다. 이름·입력·장면 힌트 등 위치에 연결된 정보는 새 스크립트에서 다시 준비합니다.

### 패치 검사

기존 프로젝트를 선택하고 **기타 설정 → 패치 검사**를 체크합니다. 단독으로 실행하거나 번역 뒤에 함께 실행할 수 있습니다.

RPG Maker는 번역·번역 재개·결과 재생성 뒤에 빠른 정적 검사를 한 번 수행합니다. JSON·JavaScript 문법, 번역 위치의 실제 값, 이벤트 구조와 선택지·원문 병기의 대응을 확인합니다. 게임을 실행하는 화면 검사는 아니며, 원문 유지 항목은 별도로 보고합니다.

```powershell
& "$tool\translate.ps1" -Command check -Project "$tool\project\YourGame-kr"
```

Ren’Py는 해당 게임에 포함된 엔진으로 검사용 원본 상태와 패치 상태를 비교합니다. 컴파일·lint·시작·화면 표시를 확인하고, 생성된 최종 문장을 렌더링합니다. 원본 리소스는 저장된 게임 경로에서 참조하므로 해당 폴더를 유지해야 합니다. 검사 작업 공간과 저장 위치는 별도로 생성합니다. 실제 게임 코드를 초기화하고 화면을 표시하는 검사이므로 시간이 걸릴 수 있습니다.

보고서는 내부 프로젝트의 `data/patch-validation.json`과 `data/validation-runs/<검사 ID>/report.json`에 저장됩니다.

검사 중에는 현재 단계와 텍스트 처리 진행 상황을 표시합니다. 번역 단계의 진행률을 검사 진행률로 이어 표시하지 않습니다. Ren’Py 검사 복사본은 성공·실패·취소 여부와 관계없이 종료 시 삭제하고 보고서·진단 로그만 남깁니다. 파일 해시를 대조하지 않으며, 같은 준비 자료로 만든 두 검사본의 결과를 비교합니다. 비교본에서도 발생한 lint 지적과 초기화 우선순위 권고는 별도 진단으로 기록하고, 새로 발생한 패치 오류와 구분합니다.

| 분류 | 의미 |
| --- | --- |
| `ours` | 비교 결과와 생성 코드·문자열 근거로 우리 패치에서 발생한 것으로 확인 |
| `original` | 동등한 원본 검사에서도 같은 게임 코드 오류가 확인 |
| `mixed` | 서로 다른 분류의 오류가 함께 확인 |
| `unknown` | 비교 환경이나 오류 원인에 충분한 근거가 없음 |

검사 도구의 오류나 지원되지 않는 검사 경로를 원본 게임 오류로 분류하지 않습니다. `passed`는 보고서에 적힌 검사 범위가 통과했다는 뜻입니다. 모든 퀘스트·저장 상태·커스텀 화면의 동작을 보장하지는 않습니다. RPG Maker는 준비된 JSON·플러그인 등의 정적 검사 결과와 실행 검사 여부를 구분해 기록합니다.

자동 보정은 원인이 확인되고 보정 규칙이 구현된 Ren’Py 생성 패치에만 적용합니다. 검사용 복사본에서 한 번 보정하고 재검사에 통과한 파일만 반영합니다. 변경 전 파일을 검사 기록에 백업하며, 원본 게임 코드와 번역 캐시는 수정하지 않습니다. 누락된 게임 레이블이나 퀘스트 조건은 임의로 만들거나 건너뛰지 않습니다. RPG Maker의 정적 검사는 문제를 보고하며, 임의로 게임 코드나 이벤트를 수정하지 않습니다.

### 추론 런타임 버전

배포본에는 기본 Ollama 0.40.15와 호환용 0.34.2가 함께 들어 있습니다. **설정 및 정보 → 추론 런타임**에서 설치된 Ollama 버전을 선택하고 **설정 저장**을 누릅니다. 다른 버전은 **다른 버전 설치 → 버전 목록 불러오기**에서 내려받습니다. 실행 중에는 버전 변경·설치가 잠깁니다.

버전별 실행파일과 라이브러리는 `runtimes/ollama-<버전>`에 보관하며, 모델은 `models/ollama`를 공유합니다. 로그에는 실제 사용한 버전과 실행 경로를 남깁니다.

```powershell
& "$tool\translate.ps1" -Command run -Project "$tool\project\YourGame-kr" `
  -RuntimeId 'ollama-0.34.2'
```

## English

### Updating a game

Select an existing project or exported result, enable **Other settings → Game update**, and select the newer game folder. A separate revision is prepared from the new scripts. Exact text, speaker, purpose, scene scope and neighboring entries determine reuse, including text shifted by insertions in the middle. New, changed or ambiguous entries remain pending. Previous projects and results are retained.

Repeat the same two paths to resume an interrupted update. Normal translation resumes its saved snapshot; use Game update to import changed game files.

```powershell
$tool = 'E:\Tools\RenPyTranslator'
& "$tool\translate.ps1" -Command update -Project "$tool\project\YourGame-kr" -Source 'D:\Games\YourGame-new'
```

`data/update-report.json` records reuse counts and matching evidence. Location-dependent names, inputs and hints are rebuilt from the newer scripts.

### Checking a patch

Select **Other settings → Patch check**, alone or alongside translation.

RPG Maker also runs one static check after translation, resume and rebuilding. It checks JSON/JavaScript syntax, applied text locations, event structure and choice/reference correspondence. It does not launch a game or verify its runtime display; retained original text is reported separately.

```powershell
& "$tool\translate.ps1" -Command check -Project "$tool\project\YourGame-kr"
```

Ren’Py checks compare isolated original and patched workspaces using the game's matching engine. Checks cover compilation, lint, startup, supported screens and final-text rendering. Original assets are referenced through the stored source path; keep that folder available. Workspaces and saves are separate. The check initializes actual game code and displays screens.

Reports are written to `data/patch-validation.json` and `data/validation-runs/<id>/report.json`. Errors are classified as `ours`, `original`, `mixed` or `unknown` from comparison evidence. Probe defects and unsupported checks are not attributed to the original game. A pass covers the recorded checks rather than every quest, save state or custom screen. RPG Maker reports static checks separately from runtime coverage.

The check displays its current phase and text-rendering progress, resetting the preceding translation phase's progress. Ren’Py test copies are removed after success, failure or cancellation; reports and diagnostic logs remain. Checks compare results from copies of the same prepared inputs without file-hash comparisons. Shared baseline lint findings and init-priority advisories are recorded separately from new patch failures.

Ren’Py generated-patch defects can be corrected once when their cause is established and an implemented repair rule applies. Only files that pass the recheck are committed, with backups in the validation artifacts. Original game code and translation caches are retained. Missing game labels and quest conditions are never invented or bypassed. RPG Maker's static check reports problems without inventing changes to game code or events.

### Runtime versions

The release includes Ollama 0.40.15 as the default and 0.34.2 for compatibility. Open **Settings and information → Inference runtime**, select an installed Ollama version, and save settings. **Install another version → Load versions** retrieves downloadable releases. Version changes and installations are locked during a job.

Each complete runtime bundle is stored in `runtimes/ollama-<version>`. Versions share `models/ollama`. Logs record the actual runtime version and path. CLI jobs accept `-RuntimeId <installed-id>`.
