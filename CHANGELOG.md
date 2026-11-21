# 변경 이력 / Changelog

배포 파일은 [GitHub Releases](https://github.com/ironhistorian7/RenPyTranslator/releases)에서 받을 수 있습니다.

## 한국어

### v0.4.0 — 2026-11-22

#### 지원 엔진 확대

- KiriKiri의 KAG `.ks`와 지원되는 비암호화 KAGEX PSB v2 `.scn`을 번역하고 XP3 패치를 생성합니다. 대사·이름표·선택지와 일부 메뉴 및 저장 화면을 한국어로 표시합니다.
- Unity Windows 빌드의 `resources.assets`에 있는 일부 TextAsset 번역을 지원합니다. 패치 적용 후 게임에서 번역에 사용한 원문 언어를 선택하면 한국어가 표시됩니다.

#### 번역 확인과 복구

- Ren’Py·RPG Maker·KiriKiri에서 번역 전에 인명 표기를 확인하고, 패치 생성 전에 미번역·부분 번역을 직접 수정할 수 있습니다. 확정한 값은 작업을 재개할 때도 재사용합니다.
- 일본어 번역을 재개할 때 가나·한자가 남은 자동 번역만 다시 처리합니다. 복구 후에도 일부 원문이 남은 한국어 초안은 마지막 확인창에서 수정할 수 있습니다.
- RPG Maker MV/MZ의 다국어 문구·선택지·인명 처리를 보완했습니다. 결과 폴더를 선택해 패치를 검사하면 실제 내보낸 파일을 검사합니다.

#### GPU와 모델

- LIGHT·FULL에 Ollama 0.40.15의 공식 ROCm 백엔드를 포함합니다. 호환 Windows AMD 드라이버에서 장치가 인식되면 ROCm을 사용합니다. ROCm을 사용할 수 없으면 확인된 Vulkan 경로로 전환합니다. NVIDIA는 CUDA를 사용합니다.
- 여러 GPU를 쓰면 장치별 모델, 동시 처리 수, 컨텍스트 창, KV 캐시, TXT 문맥을 설정할 수 있습니다. Ollama 구형·신형 모델 등록 형식과 Gemma 4·Qwen3.5 계열 GGUF 등록 호환성을 개선했습니다.

#### 오류 제보와 다운로드

- 작업 중단 시 **오류 내용 복사하고 닫기**가 있는 별도 팝업을 표시합니다. **오류를 제보할 때는 복사한 내용을 반드시 함께 붙여넣어 주세요.** 제보문에는 오류 ID와 기술 정보가 담기며 개인 경로, 이메일, 게임 대사와 번역문은 제외됩니다.
- LIGHT는 프로그램과 실행 환경을 포함합니다. FULL은 HY-MT2-7B Q6_K 모델을 함께 제공합니다. 선택한 배포본의 `.zip`과 모든 분할 파일을 같은 폴더에 받고 반디집으로 `.zip`을 열어 압축을 풉니다.

### v0.3.1 — 2026-11-11

#### 진행 상황과 오류 제보

- 현재 단계의 진행률, 처리 수, 경과 시간과 예상 남은 시간을 표시합니다. 처리량이 확인되지 않은 단계에서는 시간을 임의로 추정하지 않습니다.
- 재시도와 서버 재시작 시 완료한 항목을 중복 집계하지 않고, 기존 번역 재사용과 원문 유지 수를 구분합니다. 재시도까지 남은 대기 시간도 표시합니다.
- 오류 종류와 발생 단계를 짧게 안내하고, 자세한 출력은 접힌 **상세 기록**에서 확인할 수 있도록 정리했습니다.
- **오류 내용 복사**로 댓글·이슈에 붙여넣을 Markdown을 클립보드에 복사할 수 있습니다. **진단 파일 저장**은 대사·번역문·저장본·개인 경로를 제외한 기술 정보를 JSON으로 저장합니다.
- **로그 폴더 열기**가 해당 오류 기록의 폴더를 열도록 수정했습니다.

#### RPG Maker MV/MZ 호환성

- 지원하는 다국어 번역표와 플러그인 자료에서 선택한 원문 언어를 번역 입력과 원문 참고에 연결합니다. 단일언어 게임의 기존 처리도 유지합니다.
- JSON 키에 마침표·대괄호 등이 포함되거나 값 안에 JSON이 한 번 더 들어 있는 경우, 번역 위치를 잘못 해석하던 문제를 수정했습니다.
- 캐릭터 DB의 번역된 이름을 화자 이름표와 이름 변경 명령에 재사용합니다. 기존 **인명 표기 보정**으로도 적용할 수 있으며, 이 작업은 모델을 호출하거나 본문·별명을 다시 번역하지 않습니다.
- 원문·한국어 병기의 줄바꿈, 대사 페이지 전환과 표시 위치를 보정하고, 지원하는 자동 줄바꿈·다국어 플러그인과의 충돌을 줄였습니다.
- 플러그인 문구 후보와 동작용 식별자 유지를 참고 정보로 구분했습니다. 프로그램에 없는 설정을 켜라는 안내는 제거하고, 실제 파일 읽기 실패는 별도로 알립니다.

#### 번역 응답과 복구

- 요청 분량에 맞춰 응답 길이를 조정하고, 긴 항목은 문장·문단 경계로 나눠 처리하도록 개선했습니다. 잘린 응답을 단순한 응답 번호 누락으로 처리하던 경로를 수정했습니다.
- 응답 번호와 보호 표시의 일부 변형, 불필요한 응답 태그와 서식 복원을 보강했습니다. RPG Maker에서도 복구 가능한 저장 응답을 다시 번역하기 전에 활용하도록 확장했습니다.
- 일본어의 잔여 구절 교정과 RPG Maker 항목의 용도 구분을 보정하고, 게임 동작에 쓰이는 식별자는 원문 자료를 기준으로 적용과 검사에서 공유합니다.
- 일본어 경로·작업 요청을 GUI에서 CLI로 전달할 때의 UTF-8 처리와 실행 전 도우미 오류 안내를 보강했습니다.

#### 패치 검사와 파일 정리

- Ren’Py 검사에서 검사 도구 자체의 문제, 원본에서도 발생하는 문제와 생성 패치의 오류를 구분하도록 보강했습니다. 확인된 생성 패치 오류에만 알려진 자동 보정 규칙을 적용합니다.
- RPG Maker의 기존 JSON·JavaScript·제어 표시 정적 검사를 번역 위치와 이벤트·선택지 구조까지 확대했습니다. 이 검사는 게임 실행이나 전체 플레이 검사를 뜻하지 않습니다.
- 패치 검사용 복사본은 성공·실패·취소 시 정리하고, 보고서와 진단 로그는 남깁니다.
- 번역·재번역·게임 업데이트를 시작할 때 프로그램 폴더 전체의 7일 이상 지난 진단 로그를 정리합니다. 번역 캐시·모델·게임 파일·저장본·배포 ZIP은 유지합니다.

#### 개발·배포

- 로컬 EXE 갱신 시 실행 중인 프로그램과 파일 잠금을 확인합니다. 구성요소 교체에 실패하면 이전 파일을 복원하도록 보강했습니다.
- 소스와 배포 메타데이터를 0.3.1로 맞췄습니다. LIGHT와 FULL은 같은 프로그램 기능을 제공하며, FULL에만 지정된 HY-MT2-7B Q6_K 모델을 포함합니다.

배포 참고: `.zip`과 모든 분할 파일을 같은 폴더에 받은 뒤 반디집으로 압축을 풉니다. 각 파일은 GitHub의 파일당 업로드 제한보다 작게 생성합니다.

---

## English

Download release files from [GitHub Releases](https://github.com/ironhistorian7/RenPyTranslator/releases).

### v0.4.0 — 2026-11-22

#### Engine support

- KiriKiri supports KAG `.ks` and selected unencrypted KAGEX PSB v2 `.scn` scenarios and generates XP3 patches. Supported text includes dialogue, speaker names, choices and selected menus and save screens.
- Unity support covers selected TextAssets in `resources.assets` from Windows builds. Apply the patch, then select the source language used for translation in the game to display Korean.

#### Review and recovery

- Ren’Py, RPG Maker and KiriKiri let you review character names before translation and edit untranslated or partial entries before patch export. Confirmed entries are reused when resuming.
- When resuming Japanese translation, automatic translations with remaining kana or kanji are retried. Korean drafts that still contain source text can be edited in the final review.
- Improved multilingual text, choices and names in RPG Maker MV/MZ. When you select an output folder for a patch check, the app checks the exported files there.

#### GPUs and models

- LIGHT and FULL include the official ROCm backend for Ollama 0.40.15. AMD devices recognized by ROCm on a compatible Windows driver use ROCm. The app falls back to a verified Vulkan device when ROCm is unavailable. NVIDIA uses CUDA.
- Select a model, worker count, context window, KV cache and TXT context setting for each GPU in multi-GPU jobs. Improved support for legacy and newer Ollama model registration formats, and for registering Gemma 4 and Qwen3.5 GGUF models.

#### Bug reports and downloads

- A stopping error opens a dialog with a button to copy the report and close the dialog. **Always paste the copied report when filing a bug.** The report contains the error ID and technical details. It omits personal paths, email addresses, dialogue and translations.
- LIGHT includes the app and its runtimes. FULL also includes HY-MT2-7B Q6_K. Download the chosen `.zip` and every matching split volume to one folder, then open the `.zip` with Bandizip.

### v0.3.1 — 2026-11-11

#### Progress and bug reports

- Added per-phase progress, processed counts, elapsed time and remaining-time estimates. Phases without a measurable workload remain indeterminate.
- Retries and server restarts no longer count completed items twice. Reused translations and original-language fallbacks are reported separately, with a retry countdown.
- Added concise error summaries and moved raw output into collapsed details.
- **Copy error details** copies Markdown suitable for comments and issues to the clipboard. **Save diagnostic file** exports technical information as JSON, excluding dialogue, translations, saves and personal paths.
- **Open log folder** now opens the folder containing the relevant error record.

#### RPG Maker MV/MZ compatibility

- Connected the selected source language to translation input and original references for supported locale tables and language plugins, while retaining the single-language workflow.
- Fixed translation locations containing literal JSON keys with dots or brackets, including nested JSON stored inside strings.
- Reused translated actor database names in speaker labels and character-name commands. The existing **Character-name consistency** repair applies this without model inference or retranslating dialogue and nicknames.
- Improved bilingual wrapping, message-page transitions and positioning, and reduced conflicts with supported automatic-wrapping and language plugins.
- Separated plugin-text candidates and preserved identifiers from actual errors. Removed instructions for settings absent from this application; unreadable files are reported separately.

#### Translation responses and recovery

- Adjusted response budgets to the workload and improved splitting of long entries at sentence or paragraph boundaries. Truncated responses are no longer treated simply as missing response IDs.
- Improved handling of some response-ID and placeholder variants, unwanted response wrappers and formatting restoration. Extended saved-response recovery to RPG Maker before retranslating affected entries.
- Refined Japanese-fragment correction and RPG Maker entry purposes. Applying and checking translations now share identifiers collected from the original snapshot.
- Improved UTF-8 transfer of Japanese paths and job requests between the GUI and CLI, plus diagnostics for helper failures before a job starts.

#### Patch checks and cleanup

- Improved attribution of Ren’Py failures to the checker, the original game or the generated patch. Automatic repair remains limited to confirmed generated-patch defects with known repair rules.
- Expanded the existing RPG Maker JSON, JavaScript and control-code checks to cover translation locations and event and choice structures. These checks do not launch or play through the game.
- Validation copies are cleaned up after success, failure or cancellation; reports and diagnostic logs remain.
- Translation, retranslation and game updates remove diagnostic logs at least seven days old throughout the application folder, retaining translation caches, models, game files, saves and release ZIPs.

#### Development and distribution

- Local EXE updates check running applications and locked files, and restore previous components if replacement fails.
- Aligned source and distribution metadata at 0.3.1. LIGHT and FULL provide the same application features; only FULL includes the locked HY-MT2-7B Q6_K model.

Distribution note: Download the `.zip` and all accompanying volumes into one folder and extract with Bandizip. Each volume is kept below GitHub's per-file upload limit.
