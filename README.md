# RenPyTranslator

## 한국어

Ren’Py와 RPG Maker MV/MZ 게임을 로컬 AI로 한국어 번역하는 Windows 포터블 프로그램입니다. GUI와 PowerShell CLI를 지원하며, 번역 결과는 원본 게임에 복사해 적용합니다.

### 주요 기능

- 영어·일본어 원문을 한국어로 번역
- 한국어 아래에 원문을 55% 크기로 표시, 원문 표시 켜기·끄기
- 한국어 글꼴·기본 메뉴 보정과 대화창 높이 조정
- 중단된 번역 재개, 미번역 복구, 별도 재번역 결과로 비교
- 단계별 진행률·예상 남은 시간, 오류 제보 내용 복사와 진단 파일 저장
- 게임 업데이트 시 기존 번역 재사용과 추가·변경분 번역
- 원본·패치 비교 검사와 확인된 생성 패치 오류 보정
- 모델 다운로드·선택·삭제 및 로컬 GGUF 사용
- TXT 문맥 자료 자동 적용
- Ren’Py 인명·호칭·입력 문구 보정, 선택지 힌트<sup>beta</sup>·입력 정답 표시<sup>beta</sup>
- RPG Maker 캐릭터 DB 표기를 재사용하는 인명 보정
- Ollama 실행 환경 버전별 설치·선택

### 지원 환경

**Windows x64**가 필요합니다. 포터블 배포본에는 실행 환경이 포함되어 있어 Python·Node.js·Ollama를 따로 설치하지 않아도 됩니다.

| 항목 | 지원 범위 |
| --- | --- |
| 게임 엔진 | Ren’Py 6.99 계열, 7.3.5~7.8.7, 8.x / RPG Maker MV·MZ |
| 원문 언어 | 영어·일본어, 시작 전에 직접 선택 |
| 번역 언어 | 한국어 |
| 권장 GPU | HY-MT2-7B Q6_K 기준 VRAM 12GB |

6·8GB GPU용 설정도 제공합니다. 작은 GPU 설정은 12GB 카드의 메모리 예산 시험을 바탕으로 하며, 12GB 초과 GPU 설정은 추정값입니다. 자세한 기준은 [모델과 GPU 설정](docs/models.md)을 참고하세요. 게임의 커스텀 화면·플러그인에 따라 추가 호환 작업이 필요할 수 있습니다.

### 다운로드와 실행

[GitHub Releases](https://github.com/ironhistorian7/RenPyTranslator/releases)에서 배포본을 받습니다. **버전별 변경 이력도 Releases에서 확인할 수 있습니다.**

| 배포본 | 포함 내용 |
| --- | --- |
| **LIGHT** | 프로그램과 실행 환경. 번역 모델은 별도로 준비 |
| **FULL** | LIGHT와 동일한 기능 + HY-MT2-7B Q6_K |

선택한 배포본의 `.zip`과 같은 이름의 모든 `.z01`, `.z02` 등의 파일을 같은 폴더에 받습니다. **반디집으로 `.zip`을 열어** 압축을 풉니다. 분할 파일이 없는 배포본은 `.zip` 하나만 받으면 됩니다.

1. 압축을 푼 폴더에서 `RenPyTranslator.exe`를 실행합니다.
2. **AI 모델**에서 사용할 모델을 선택하고 적용합니다. LIGHT는 모델을 다운로드하거나 `models` 아래에 GGUF 파일을 넣은 뒤 목록을 새로고침합니다.
3. **번역 → 게임 선택**에서 게임 실행 파일이 있는 폴더와 원문 언어를 선택합니다.
4. **모델 확인 → 출력 설정 → 최종 확인**을 거쳐 번역을 시작합니다.
5. 완료 페이지에서 결과 폴더를 열어 패치를 적용합니다.

프로그램을 옮길 때는 EXE만 옮기지 말고 폴더 전체를 복사하세요. 포함된 기본·호환용 Ollama는 **설정 및 정보 → 추론 런타임**에서 선택합니다.

### 패치 적용

| 게임 엔진 | 적용 방법 |
| --- | --- |
| Ren’Py | 결과의 `game` 폴더를 원본의 `game` 폴더에 합칩니다. `game/game`으로 중첩하지 않습니다. |
| RPG Maker MV/MZ | 결과의 `APPLY.txt`를 따라 `data`·`js`·`fonts`를 원본의 같은 위치에 덮어씁니다. `www`가 있으면 폴더 구조를 유지합니다. |

패치는 번역에 사용한 것과 **같은 버전의 게임**에 적용합니다. 적용 후에는 평소처럼 게임 실행 파일을 엽니다.

기본 결과 위치는 `project/<게임명>-kr`입니다. `data/projects`는 설정·스크립트·번역 캐시가 저장되는 내부 작업 공간입니다.

### 자세한 안내

| 문서 | 내용 |
| --- | --- |
| [사용 안내](docs/usage.md) | 단계별 화면, 기존 결과 보정, 재개·재번역 |
| [모델과 GPU 설정](docs/models.md) | 모델 준비, GPU 프리셋, 고급 설정 |
| [CLI 사용법](docs/cli.md) | PowerShell 명령어와 엔진별 지원 작업 |
| [문맥 자료](docs/context-runtime.md) | TXT 위치·형식, 장면 범위, 적용 확인 |
| [오류와 로그](docs/diagnostics.md) | 실패 기록, 서버 복구, 문제 확인 |
| [게임 업데이트와 패치 검사](docs/updates-and-checks.md) | 번역 재사용, 실행 검사, 오류 분류와 자동 보정 |
| [개발 및 배포](docs/development.md) | 소스 준비, 테스트, 포터블 빌드 |

번역 요청은 로컬에서 처리합니다. 모델 조회·다운로드에는 인터넷이 필요합니다. Grok 등 외부 서비스로 문맥을 준비하는 작업은 선택 사항이며 프로그램이 자동 실행하지 않습니다.

### 사용한 오픈소스

RPG Maker MV/MZ 지원에는 [Gameloc](https://github.com/eeewsm123123/gameloc) 3.1.1의 엔진 감지·텍스트 추출·번역 적용·제어문자 문법·플러그인 등록 코드를 사용합니다. 해당 소스와 **GPL-3.0-or-later** 라이선스를 프로그램에 포함하며, Gameloc을 따로 설치할 필요는 없습니다. 로컬 AI 번역·GUI·캐시·문맥 처리와 원문 병기는 RenPyTranslator에서 구현하고, Ren’Py는 기존 번역 경로를 사용합니다.

[라이선스](LICENSE) · [외부 구성요소 고지](THIRD_PARTY_NOTICES.md)

---

## English

RenPyTranslator is a portable Windows application that translates Ren’Py and RPG Maker MV/MZ games into Korean using local AI. It supports a desktop GUI and a PowerShell CLI, and produces patches to copy into the original game.

### Features

- English or Japanese source text translated into Korean
- Original text below Korean at 55% size, with a visibility setting
- Korean fonts, standard-menu corrections and textbox height adjustment
- Resumable translation, failed-item recovery and separate retranslation results
- Per-phase progress and time estimates, copyable bug reports and diagnostic export
- Reuse existing translations when updating a game; translate additions and changes
- Compare original and patched workspaces and repair confirmed generated-patch defects
- Model downloads, selection, deletion and local GGUF files
- Automatic TXT context support
- Ren’Py name, address and input corrections, choice hints<sup>beta</sup> and answer prompts<sup>beta</sup>
- RPG Maker name consistency using translated actor database spellings
- Install and select versioned Ollama runtimes

### Requirements and support

**Windows x64** is required. Portable releases include their execution environment; system Python, Node.js and Ollama are not needed.

| Item | Support |
| --- | --- |
| Engines | Ren’Py 6.99, 7.3.5–7.8.7, 8.x / RPG Maker MV and MZ |
| Source language | English or Japanese, selected before translation |
| Output language | Korean |
| Recommended GPU | 12GB VRAM for HY-MT2-7B Q6_K |

Profiles are also available for 6GB and 8GB GPUs. These are based on memory-budget tests on a 12GB card; profiles above 12GB are estimates. See [Models and GPU settings](docs/models.md). Custom screens and plugins may need additional compatibility work.

### Download and run

Download a release from [GitHub Releases](https://github.com/ironhistorian7/RenPyTranslator/releases). **Version history is available there as well.**

| Edition | Contents |
| --- | --- |
| **LIGHT** | Application and runtimes; prepare a translation model separately |
| **FULL** | The same features as LIGHT, plus HY-MT2-7B Q6_K |

Download the selected edition's `.zip` and every matching `.z01`, `.z02`, etc. into the same folder, then **open the `.zip` with Bandizip**. If no split files are listed, only the `.zip` is needed.

1. Run `RenPyTranslator.exe` from the extracted folder.
2. Select and apply a model in **AI Model**. For LIGHT, download a model or place a GGUF below `models` and refresh the list.
3. In **Translation → Game**, select the folder containing the game executable and choose the source language.
4. Continue through **Model → Output → Review** and start translation.
5. Open the result folder from the completion page and apply the patch.

Move the entire portable folder, rather than the EXE alone. Select the bundled default or compatibility Ollama runtime under **Settings and information → Inference runtime**.

### Apply a patch

| Engine | Installation |
| --- | --- |
| Ren’Py | Merge the result's `game` folder into the original `game`. Do not create `game/game`. |
| RPG Maker MV/MZ | Follow `APPLY.txt` to replace `data`, `js` and `fonts` at their original locations. Preserve the `www` structure when present. |

Apply patches to the **same game version** used for translation, then run the game's normal executable.

The default result location is `project/<game>-kr`. `data/projects` holds internal settings, scripts and translation caches.

### Documentation

| Guide | Contents |
| --- | --- |
| [Usage](docs/usage.md) | Wizard steps, existing-result repairs, resume and retranslation |
| [Models and GPU settings](docs/models.md) | Model preparation, GPU presets and advanced settings |
| [CLI](docs/cli.md) | PowerShell commands and engine-specific operations |
| [Context](docs/context-runtime.md) | TXT locations, formats, scene scopes and verification |
| [Diagnostics](docs/diagnostics.md) | Failure records, server recovery and troubleshooting |
| [Game updates and patch checks](docs/updates-and-checks.md) | Translation reuse, engine checks, attribution and repairs |
| [Development and distribution](docs/development.md) | Source setup, checks and portable builds |

Translation requests run locally. Model discovery and downloads use the internet. Preparing context with an external service such as Grok is optional and is not started automatically.

### Open-source acknowledgements

RPG Maker MV/MZ support uses [Gameloc](https://github.com/eeewsm123123/gameloc) 3.1.1 code for engine detection, text extraction, translation application, control-code grammar and plugin registration. Its source and **GPL-3.0-or-later** license are bundled; no separate Gameloc installation is required. Local AI translation, the GUI, caching, context handling and bilingual presentation are implemented in RenPyTranslator. Ren’Py retains its existing translation pipeline.

[License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)
