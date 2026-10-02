# RenPyTranslator

## 한국어

Ren’Py 게임을 로컬 AI로 한국어 번역하고, 복사해서 적용할 수 있는 패치를 만드는 Windows 포터블 도구입니다. GUI와 PowerShell CLI를 모두 지원합니다.

### 일반 사용자: 포터블 ZIP 실행

1. Releases에서 ZIP을 받고 폴더 전체를 압축 해제합니다.
2. `RenPyTranslator.exe`를 실행합니다. EXE만 따로 옮기지 마세요.
3. **AI 모델** 탭에서 설치된 모델을 선택하고 적용합니다. LIGHT에는 모델이 없으므로 먼저 다운로드하거나 GGUF를 넣어야 합니다.
4. 게임 실행 파일이 있는 폴더를 선택하고 **번역 시작**을 누릅니다.
5. 완료된 결과의 `game` 폴더를 원본 게임의 `game` 폴더와 합칩니다. 평소처럼 게임 EXE로 실행합니다. `game/game`으로 중첩하지 마세요.

| 배포본 | 포함 내용 |
| --- | --- |
| LIGHT | GUI·CLI·Python·Electron·Ollama·글꼴. AI 모델 제외 |
| FULL | LIGHT와 동일한 기능 + 검증된 HY-MT2-7B Q6_K 하나 |

포터블 사용자는 Python·Node.js·Ollama를 별도로 설치하지 않아도 됩니다. Windows x64와 GPU에 맞는 드라이버가 필요합니다. HY Q6_K의 권장 기준은 VRAM 12GB이며, 다른 모델의 메모리 요구량은 다릅니다. LIGHT도 실행 런타임을 포함하므로 수 GB가 될 수 있습니다.

번역 요청은 로컬에서 처리합니다. 준비 스크립트와 모델 목록 조회·다운로드에는 인터넷을 사용합니다. Grok 등의 외부 서비스로 문맥을 만드는 작업은 별도이며 자동으로 실행하지 않습니다.

### 모델 보관

`models` 아래 어느 하위 폴더에든 단일 GGUF를 넣고 AI 모델 탭에서 새로고침할 수 있습니다. 앱에서 받은 모델은 `models/ollama/blobs`에 저장합니다. 모델 선택·적용, 설치됨 표시, 중복 다운로드 방지, 폴더 열기와 삭제 기능을 제공합니다. 모델을 선택하지 않으면 번역을 시작하지 않고 AI 모델 탭으로 안내합니다.

프로그램은 내장 Ollama를 자체 포트와 모델 저장소로 실행합니다. 컴퓨터에 별도로 설치한 Ollama의 모델에 의존하지 않습니다. 포터블 폴더 전체를 옮기면 모델도 함께 이동합니다.

### 개발자: 소스 준비

소스 저장소에는 Python 3.12 x64가 필요합니다. Python이 없다면 `setup.ps1`이 설치 안내를 표시합니다. 실행용 라이브러리는 `requirements.txt`, 빌드 도구는 `requirements-build.txt`에 고정합니다. 전역 Python에 패키지를 설치하지 않습니다.

저장소 루트에서 실행하세요.

```powershell
.\setup.ps1
.\gui.ps1 -SourceMode
```

`setup.ps1`은 저장소의 `.venv`를 만들고 의존성을 설치하며, `runtime-lock.json`에 고정한 Node.js·Ollama를 `runtimes`에 다운로드합니다. SHA-256을 확인하고 `desktop/package-lock.json`으로 `npm ci`와 화면 빌드를 실행합니다. 기존 모델·사용자 설정·프로젝트·번역 캐시는 유지합니다. AI 모델은 이 단계에서 다운로드하지 않습니다.

Python을 직접 지정하려면:

```powershell
.\setup.ps1 -PythonExe 'C:\path\to\python.exe'
```

`.\gui.ps1`은 작업 폴더의 EXE가 있으면 이를 실행합니다. `-SourceMode`는 로컬 Electron과 수정한 소스를 실행합니다. CLI 래퍼는 소스와 `.venv`가 함께 있으면 소스를, 포터블 배포본에서는 EXE를 사용합니다. 시스템 PATH나 전역 Ollama 설정은 바꾸지 않습니다.

### 배포 빌드

```powershell
.\build.ps1                    # 기본: LIGHT, 모델 없이 빌드
.\build.ps1 -Edition Full      # 고정된 공식 HY Q6_K를 확인/준비하여 포함
.\build.ps1 -Edition Both      # 동일한 앱 빌드로 두 배포본 생성
```

결과는 `dist/RenPyTranslator-light.zip`, `dist/RenPyTranslator-full.zip`과 `dist/RenPyTranslator/`에 생성합니다. FULL에 필요한 HY가 없으면 고정된 공식 파일만 다운로드하고 해시를 검증합니다. 다른 모델이나 게임·설정·번역 캐시는 배포본에 넣지 않습니다. 기존 루트의 보관 ZIP은 유지합니다.

빌드한 EXE와 앱 실행 폴더는 저장소 루트에도 갱신하여 바로 실행할 수 있게 합니다. ZIP은 소스 레포에 넣지 말고 Releases에 게시하세요. `.gitignore`는 ZIP·`dist`·런타임·모델·캐시를 제외합니다. Git 저장소를 자동 생성하지 않습니다.

글꼴과 unrpyc 소스·라이선스는 `vendor`에 보관합니다. 버전·출처는 `vendor/versions.json`, 파일 해시는 `vendor/asset-lock.json`, 런타임과 FULL 모델 정보는 `runtime-lock.json`에서 관리합니다. [외부 구성요소 고지](THIRD_PARTY_NOTICES.md)를 함께 배포합니다.

### CLI 사용

옵션 없이 실행하면 도움말만 표시합니다. 게임을 자동 실행하지 않습니다.

```powershell
.\translate.ps1
.\translate.ps1 -Command run -Source 'D:\Games\YourGame'
.\translate.ps1 -Command run -Project '.\project\YourGame-kr'
.\translate.ps1 -Command retranslate -Project '.\project\YourGame-kr'
```

`run`은 저장된 번역을 건너뛰고 이어갑니다. `retranslate`는 이전 결과를 보존하고 별도 프로젝트로 다시 번역하여 모델·문맥의 차이를 비교합니다. 중단된 재번역도 동일한 프로젝트·모델로 재개할 수 있습니다.

기존 결과를 고칠 때는 필요한 범위만 지정합니다. GUI의 고급 설정에서도 개별 작업을 체크할 수 있으며, 복구 작업은 접힌 별도 영역에 모여 있습니다.

```powershell
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix font
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix names
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix failed
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix display
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix layout -TextboxScale default
.\translate.ps1 -Command language -Project '.\project\YourGame-kr' -LanguageCorner right -LanguageMargin 12
.\translate.ps1 -Command tasks -Project '.\project\YourGame-kr' -Tasks answers,routes
```

`font`: 글꼴·기본 메뉴·표시, `names`: 인명·호칭·입력 문구, `failed`: 기록된 실패만 복구, `display`: 원문 참고와 표시, `layout`: 대화창 높이만, `all`: 전체 보정입니다. `names`, `failed`, 장면 힌트의 누락 요약에는 로컬 모델이 필요할 수 있습니다. 정답·선택지 힌트는 선택했을 때만 넣습니다. 지원되는 상태 변화·이동·미디어 단서를 사용하며, 모든 게임의 동적 분기나 이미지 내용을 해석하지는 않습니다.

결과 위치·접미사·대화창 높이를 바꾸려면 `-Output`, `-Suffix`, `-TextboxScale`을 사용하세요. `-Model`로 모델을 지정할 수 있습니다. 자세한 명령은 `-Help`에 있습니다.

### 번역과 문맥

대사는 한국어 위·영문 아래로 표시하며, 참고 영문의 크기는 한국어의 55%입니다. 대화창 높이는 게임 기본값을 사용합니다. 인명 번역은 게임의 기본 이름을 기준으로 하며, 사용자가 변경한 이름은 한국어 번역에 반영하지 않습니다. 별명·호칭 입력은 인명과 구분합니다. 기본 메뉴와 퀵바는 고정 번역을 사용하고, 게임 고유 커스텀 메뉴는 영어를 유지할 수 있습니다. 일반 글꼴은 나눔스퀘어네오, 손글씨는 나눔펜이며 아이콘용 글꼴은 보존합니다.

선택한 게임의 `game/context`에 UTF-8 TXT 문맥 자료가 있으면 기본 번역에서 자동으로 읽습니다. 없으면 건너뜁니다. 공통 배경, 파일·장면 적용 범위, 인물 관계·말투 변화와 용어를 정리해 넣을 수 있습니다. 작성 권장량 4,500토큰은 고정 상한이 아니며, 요청 전체의 실제 여유에 맞춰 문맥을 조절합니다. 가까운 원문을 우선하고 대사를 중간에서 자르지 않습니다. 말투와 호칭의 정확성을 보장하지는 않습니다.

기본 HY 요청 창은 16K입니다. 별도 범용 모델로 전체 이야기를 분석하는 과정은 자동 연결하지 않습니다. 이름·UI 라벨 번역에는 대사 문맥을 섞지 않습니다. 문맥 형식·주입 확인법은 [문맥 실행 안내](docs/context-runtime.md), 외부 문맥 준비 지침은 [Grok용 안내](docs/grok-context-preparation.txt)를 참고하세요.

### 파일과 오류 기록

| 위치 | 용도 |
| --- | --- |
| `project/<게임명>-kr` | 적용할 패치와 결과 ZIP. 기본 결과 위치 |
| `data/projects/<게임명>` | 내부 작업 설정·스크립트·번역 캐시 |
| 내부 프로젝트의 `data/translations.jsonl` | 저장된 번역 |
| 내부 프로젝트의 `data/translation-requests.jsonl` | 요청 설정·문맥 적용 진단 |
| 내부 프로젝트의 `data/failed-items.json`, `data/failed-repair-report.json` | 미번역 및 복구 기록 |
| 내부 프로젝트의 `data/errors` | 작업 단계·항목·예외·추적 정보를 담은 상세 오류 |
| `logs` | GUI·로컬 서버·GPU/충돌 진단 로그 |

새 작업은 코드·글꼴 등 처리에 필요한 파일만 복사합니다. 이미지·영상·음악은 원본 위치를 참조하므로 작업 중 원본을 이동하지 마세요. 기존 작업 폴더의 내용은 자동 정리하지 않습니다.

취소·오류 시 저장된 번역은 유지하고 모델을 정리합니다. 저장되기 전 진행 중 배치는 다시 처리할 수 있습니다. 복구 가능한 서버 오류는 기록 후 제한적으로 재시작하며, 반복 실패는 상세 로그와 함께 종료합니다. 재개는 같은 프로젝트로 `run`을 실행하세요. `verify-source`는 원본 변경 확인을 명시적으로 요청할 때 사용합니다.

호환 목표는 Ren’Py 7.3.5~7.8.7 및 8.x입니다. 게임에 포함된 엔진을 사용하며, 개조된 화면·문자 태그·동적 스크립트는 별도 확인이 필요할 수 있습니다. 모든 게임/버전 조합을 시험했다는 의미는 아닙니다.

개발 검증:

```powershell
& '.\.venv\Scripts\python.exe' -B -X utf8 -m unittest discover -s app
& '.\RenPyTranslator-cli.exe' --self-check
```

자체 점검은 게임이나 모델 추론을 시작하지 않습니다. ZIP을 다른 폴더에 풀어 같은 점검을 수행하면 경로 의존성을 확인할 수 있습니다.

---

## English

RenPyTranslator creates Korean translation patches for Ren’Py games using local AI. It is a portable Windows application with both a desktop GUI and a PowerShell CLI.

### Users: run the portable ZIP

1. Download a ZIP from Releases and extract the entire folder.
2. Run `RenPyTranslator.exe`. Keep its accompanying folders beside it.
3. Select and apply an installed model in **AI Model**. LIGHT requires a model download or a local GGUF first.
4. Select the folder containing the game's executable and start translation.
5. Merge the resulting `game` folder into the original game's `game` folder, then run the game's normal EXE. Do not create `game/game`.

| Edition | Contents |
| --- | --- |
| LIGHT | GUI, CLI, Python, Electron, Ollama and fonts; no AI model |
| FULL | The same features as LIGHT, plus one verified HY-MT2-7B Q6_K model |

Portable users do not need system Python, Node.js or Ollama. Windows x64 and compatible GPU drivers are required. The recommended baseline for HY Q6_K is 12GB VRAM; other models have different requirements. LIGHT still includes substantial runtimes and can occupy several GB.

Translation requests run locally. Developer setup, model discovery and downloads use the internet. Preparing context with an external service such as Grok is a separate, user-managed step.

### Model storage

Place a single GGUF anywhere below `models`, then refresh the AI Model list. In-app downloads go into `models/ollama/blobs`. The GUI supports selection and application, installed indicators, duplicate-download prevention, opening the model folder, and deletion. Translation without an applied model shows guidance to the AI Model tab.

The tool starts its bundled Ollama with its own port and model store. It does not rely on a system Ollama installation. Move the entire portable folder to move its models with it.

### Developers: prepare the source

Install Python 3.12 x64 for source development. If it is missing, `setup.ps1` prints installation instructions. Runtime libraries are pinned in `requirements.txt`; build tools are pinned in `requirements-build.txt`. Packages are installed only in the repository's `.venv`.

Run from the repository root:

```powershell
.\setup.ps1
.\gui.ps1 -SourceMode
```

Setup creates the local venv, installs dependencies, downloads the Node.js and Ollama versions locked in `runtime-lock.json` into `runtimes`, checks SHA-256, and runs `npm ci` plus the frontend build using `desktop/package-lock.json`. Existing models, settings, projects and translation caches are retained. Setup does not download an AI model.

To specify Python explicitly:

```powershell
.\setup.ps1 -PythonExe 'C:\path\to\python.exe'
```

`.\gui.ps1` runs the local EXE when available. `-SourceMode` runs repository sources with local Electron. The CLI wrapper uses source Python when both the source entrypoint and venv exist; otherwise it uses the portable CLI EXE. Setup does not change system PATH or global Ollama settings.

### Build a release

```powershell
.\build.ps1                    # Default: LIGHT; no model needed
.\build.ps1 -Edition Full      # Verify/prepare the pinned official HY Q6_K
.\build.ps1 -Edition Both      # Produce both editions from the same app build
```

Outputs are `dist/RenPyTranslator-light.zip`, `dist/RenPyTranslator-full.zip`, and `dist/RenPyTranslator/`. FULL downloads only the pinned official HY file when missing and verifies its hash. Other models, games, personal settings and translation caches are excluded. Previously saved ZIPs in the repository root are retained.

The builder also updates the local EXEs and application runtime folders beside the source so they can be launched directly. Publish ZIPs in Releases rather than committing them. `.gitignore` excludes archives, `dist`, runtimes, models and caches. No Git repository is created automatically.

Fonts, unrpyc sources and licenses are vendored. Provenance is in `vendor/versions.json`, integrity hashes in `vendor/asset-lock.json`, and pinned runtime/FULL model assets in `runtime-lock.json`. Include [third-party notices](THIRD_PARTY_NOTICES.md) when distributing the application.

### CLI

Running without a command prints help only. Translation does not automatically launch the game.

```powershell
.\translate.ps1
.\translate.ps1 -Command run -Source 'D:\Games\YourGame'
.\translate.ps1 -Command run -Project '.\project\YourGame-kr'
.\translate.ps1 -Command retranslate -Project '.\project\YourGame-kr'
```

`run` resumes saved work and skips existing translations. `retranslate` preserves earlier results and uses a separate project for comparison. Interrupted retranslations can resume with the same project and model.

Choose only the fixes needed for an existing result. These tasks are also available as independent checkboxes in Advanced settings, with recovery tasks grouped in a collapsed section.

```powershell
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix font
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix names
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix failed
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix display
.\translate.ps1 -Command repair -Project '.\project\YourGame-kr' -Fix layout -TextboxScale default
.\translate.ps1 -Command language -Project '.\project\YourGame-kr' -LanguageCorner right -LanguageMargin 12
.\translate.ps1 -Command tasks -Project '.\project\YourGame-kr' -Tasks answers,routes
```

Scopes: `font` fixes fonts, standard menus and presentation; `names` fixes names, forms of address and input text; `failed` handles indexed failures only; `display` fixes original-language references and presentation; `layout` changes textbox height only; `all` runs all fixes. Name/failure repairs and missing scene summaries can require a local model. Answer and choice hints are added only when selected. Hints use supported state changes, destinations and media references; they do not interpret every dynamic branch or image's contents.

Use `-Output`, `-Suffix`, and `-TextboxScale` for output location, suffix and textbox height. `-Model` specifies a model. See `-Help` for available commands.

### Translation and context

Dialogue displays Korean above the original English, whose size is 55% of the Korean text. Textbox height defaults to the game's own setting. Korean character names use the game's default names even when a player changes them. Nicknames and forms of address are treated separately. Standard menus and quickbars use fixed translations; custom game menus may remain English. Regular text uses NanumSquareNeo, handwriting uses Nanum Pen, and icon fonts are preserved.

UTF-8 context files in the selected game's `game/context` are detected automatically; absent folders are skipped. Context can describe shared background, file/scene scope, relationships, speech changes and terminology. The suggested 4,500-token context size is not a fixed ceiling: inclusion adapts to the total request budget. Nearby source text takes priority, and dialogue units are not cut mid-sentence. Context does not guarantee correct Korean register or terms of address.

The default HY request window is 16K. A separate general model is not automatically used to analyze the story. Speaker/UI labels do not receive story context. See [context runtime documentation](docs/context-runtime.md) and [Grok preparation instructions](docs/grok-context-preparation.txt).

### Files and diagnostics

| Location | Purpose |
| --- | --- |
| `project/<game>-kr` | Installable patch and result ZIP; default output |
| `data/projects/<game>` | Internal configuration, scripts and translation cache |
| Internal project's `data/translations.jsonl` | Saved translations |
| Internal project's `data/translation-requests.jsonl` | Request settings and context diagnostics |
| Internal project's `data/failed-items.json`, `data/failed-repair-report.json` | Failure and recovery records |
| Internal project's `data/errors` | Detailed operation/item/exception/traceback records |
| `logs` | Desktop, local server and GPU/crash diagnostics |

New workspaces copy only processing inputs such as code and fonts. Images, video and music are read from the original location; keep the source in place while processing. Existing workspaces are not automatically pruned.

Cancellation and errors retain saved translations and clean up the model. An in-flight unsaved batch may repeat. Recoverable server failures are logged and retried through bounded restarts; repeated failures stop with detailed diagnostics. Resume with `run` on the same project. `verify-source` explicitly checks changes to the original source.

Compatibility targets are Ren’Py 7.3.5–7.8.7 and 8.x, using the game's bundled engine. Custom screens, text tags and dynamic scripts may need separate verification. This does not mean every game/version combination has been tested.

Developer checks:

```powershell
& '.\.venv\Scripts\python.exe' -B -X utf8 -m unittest discover -s app
& '.\RenPyTranslator-cli.exe' --self-check
```

Self-check starts neither a game nor model inference. Extract the ZIP to another folder and repeat it to check relocation.
