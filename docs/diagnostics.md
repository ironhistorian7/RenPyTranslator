# 오류와 로그 / Diagnostics

[README](../README.md) · [CLI](cli.md) · [문맥 자료 / Context](context-runtime.md)

## 한국어

### 오류가 났을 때

GUI의 **오류 내용 복사**는 게시글에 붙여넣을 수 있는 Markdown 요약을 클립보드에 복사합니다. **진단 파일 저장**은 더 자세한 기술 정보를 JSON 파일로 저장합니다. 두 자료에는 오류 종류·발생 단계·프로그램 및 런타임 버전·선택한 실행 설정이 담기며, 대사·번역문·저장본·프롬프트·개인 경로는 제외합니다. 원인에 대한 추정과 실제 오류 기록은 구분합니다.

**로그 폴더 열기**는 해당 오류 기록의 폴더를 엽니다. 기록이 만들어지기 전의 실패는 공통 `logs` 폴더를 엽니다. 원본 상세 로그에는 게임 문장이나 경로가 들어갈 수 있으므로, 제보할 때는 내보낸 진단 파일을 이용하세요. 작업 화면의 접힌 **상세 기록**에는 진행 중 출력된 작업 기록이 표시됩니다.

RPG Maker의 플러그인 문구 후보 수와 동작용 식별자 유지 수는 참고 정보입니다. 후보 수가 곧 미번역 대사 수를 뜻하지 않습니다. 읽지 못한 추가 자료는 파일 수와 별도 기록으로 알립니다.

같은 프로젝트로 번역을 다시 실행하면 저장된 번역을 재사용합니다. 진행 중 저장되지 않은 배치는 다시 처리할 수 있습니다. 모델이나 문맥 설정을 바꿔도 완료한 번역을 자동으로 지우지는 않습니다.

### 파일 위치

| 위치 | 내용 |
| --- | --- |
| `project/<게임명>-kr` | 적용할 패치와 결과 ZIP |
| `data/projects/<게임명>` | 내부 프로젝트와 번역 캐시 |
| 내부 프로젝트의 `data/translations.jsonl` | 저장된 번역 |
| `data/failed-items.json` | 실패해 원문으로 남은 항목과 이유 |
| 내부 프로젝트의 `output/failed-repair-report.json` | Ren’Py의 최근 복구 시도 결과 |
| `data/translation-requests.jsonl` | 요청·응답, 설정, 문맥 적용·검증 기록 |
| `data/context-report.json` | TXT 파일 읽기·장면 범위 연결 결과 |
| `data/context-history.jsonl` | 실행별 문맥 로딩 기록 |
| `data/rpg-context-scopes.json` | RPG Maker의 대화 구간 분류 근거 |
| `data/rpg-source-language.json` | RPG Maker의 선택 언어 자료·연결 항목 수·누락 또는 모호한 번역표 |
| `data/rpg-extraction-notices.json` | RPG Maker 추가 자료의 읽기 실패 기록 |
| `data/errors` | 프로젝트별 상세 오류와 서버 복구 기록 |
| 프로그램의 `logs` | GUI·모델 서버·GPU 진단 로그 |
| `logs/errors` | 프로젝트가 정해지기 전 발생한 오류 |
| `logs/desktop-helper-*.log` | 폴더·설정 조회 등 사전 작업에서 상세 오류 파일을 만들지 못한 경우의 실행 기록 |
| 프로그램의 `logs/reports` | 진단 파일 저장 대화상자의 기본 위치. 다른 위치도 선택 가능 |

표의 `data/...`는 별도 표시가 없으면 **내부 프로젝트 안의 data 폴더**입니다. 실패 목록과 복구 보고서는 기록 시점과 목적이 달라 항목 수가 같지 않을 수 있습니다.

새 작업 공간에는 스크립트·설정·글꼴 등 처리에 필요한 파일을 준비합니다. 이미지·영상·음악은 원본 위치를 참조하므로 작업 중 원본을 이동하지 않는 편이 좋습니다. 예전에 만들어진 작업 공간의 불필요한 파일을 자동 삭제하지는 않습니다.

### 로그 보관

번역·재번역·게임 업데이트를 시작할 때 프로그램 폴더 전체에서 마지막 수정 후 7일 이상 지난 실행·오류·진단 로그를 정리합니다. 현재 프로젝트뿐 아니라 다른 프로젝트의 로그도 포함합니다. 번역 캐시·실패 목록·모델·게임 파일·저장본·배포 ZIP은 유지하며, 사용 중이라 삭제하지 못한 로그는 건너뜁니다.

패치 검사용 원문·번역 복사본은 성공·실패·취소와 관계없이 종료 시 삭제합니다. 보고서와 진단 로그는 `data/validation-runs/<검사 ID>`에 남으며, 로그 보관 기간이 적용됩니다.

### 상세 오류에 남는 정보

오류 JSON에는 작업 단계, 관련 파일·항목 ID, 모델·실행 설정, 예외와 traceback, 항목별 검증 실패 이유, 재개 안내가 들어갑니다. GUI에는 짧은 오류 안내와 오류 ID를 표시합니다. 전체 traceback은 원본 오류 JSON에 보관합니다.

게임 폴더를 선택하거나 설정을 읽는 도중 발생한 오류도 같은 제보 버튼으로 자료를 준비할 수 있습니다. 이미 상세 오류 파일이 있으면 그 파일을 사용하며 같은 오류의 로그를 중복 생성하지 않습니다. 진단 파일 저장에 실패해도 원래 오류 기록은 유지됩니다.

번역 요청 기록에는 실제 프롬프트와 응답이 포함됩니다. 기록을 공유할 때 게임 대사나 개인 경로가 포함되어 있는지 확인할 수 있습니다. 파일을 읽지 못하거나 진단 정보를 수집하지 못한 경우에도 그 이유를 기록합니다.

출력 길이 초과는 `output_limit`, 정상 종료한 응답의 실제 번호 누락은 `missing_response_item`으로 구분합니다. 긴 본문은 필요한 출력 공간을 먼저 확보하고, 요청 창에 들어가지 않으면 문장·문단 단위로 나눕니다. 응답이 잘렸다면 온전한 앞 항목을 유지하고 미완료 부분만 다시 처리합니다. 분할·출력 예산 조정은 요청 기록에 남으며 완성된 번역은 원래 항목 ID로 저장합니다.

### 모델 서버 자동 복구

번역 중 모델 서버의 HTTP 500/502/503/504, 연결 끊김 또는 시간 초과가 발생하면 이미 완료된 배치를 저장하고 도구가 실행한 서버를 종료한 뒤 같은 설정으로 재시작합니다. 저장되지 않은 항목만 다시 요청하며, 동시 요청이 함께 실패해도 재시작은 한 곳에서만 처리합니다. 이전 서버 종료 실패·남아 있는 요청 스레드·사용자 취소 시에는 재시작하지 않습니다.

한 장애에서 재시작은 최대 3회이며, 차례로 **10초·30초·3분** 대기합니다. 재시작한 서버가 실제 요청에 정상 응답한 뒤 새 장애가 발생하면 다시 3회의 기회를 부여합니다. 번역 실행 전체의 누적 제한은 없습니다. 대기 중에도 취소할 수 있습니다. 문장별 출력 검증 실패는 별도 재시도를 사용하며 서버 복구 횟수에 포함하지 않습니다.

GUI에서는 자동 복구 중인 오류와 작업을 중단시킨 오류를 구분합니다. 재시도 대기 시간과 횟수를 표시하며, 복구되어 진행이 재개된 일시적 오류는 상세 기록에서 확인할 수 있습니다. 원문으로 남은 항목 수는 번역 완료 수와 별도로 표시합니다.

오류 자료는 `data/errors/runtime-<사건 ID>.json`, `.server.log`와 `runtime-recovery.jsonl`에 남습니다. HTTP 오류 본문, 요청 로그 연결, 모델·실행 옵션, 서버 시작 및 마지막 로그, 최근 GPU/시스템 메모리 추이, 서버 프로세스 메모리, 오류 시각 주변 Windows GPU/응용 프로그램 기록, 저장·재시작 결과를 포함합니다. 자원 추이는 최근 150개 샘플을 메모리에 보관하다 오류 시 저장합니다. 수집 불가 항목도 이유를 기록하며 진단 수집 실패만으로 복구를 막지 않습니다. 평상시 CUDA 동기 진단을 강제하거나 모델·문맥·캐시 설정을 자동 변경하지 않습니다. 자동 복구는 CUDA 근본 원인을 해결했다는 뜻이 아닙니다.

### GPU와 문맥 적용 기록

`GPU policy` 시작 로그, 요청 로그의 `hardware_policy`, 오류 JSON의 `settings._hardware_policy`에 감지한 총용량·선택한 프로필·요청/실제 모델·메모리 근거·TXT 제외 이유가 남습니다. TXT를 제외한 작업은 `context-report.json`에 `enabled:false`를 기록합니다. 모델 서버가 재시작되어도 같은 작업의 결정은 유지합니다. 하드웨어 감지 실패는 별도로 기록하며 없는 GPU 용량을 추측하지 않습니다.

이름표·UI 요청의 `item_purposes`는 항목 용도와 근거, `context_policy`는 제외한 이야기 문맥과 유지한 용어 정보를 담습니다. `validation`, `item_retry`, `source_fallback`에서는 검사 실패, 개별 재시도와 원문 유지 이유를 확인할 수 있습니다.

문맥 파일을 읽었다는 기록만으로 해당 내용이 모든 요청에 전달됐다고 판단할 수는 없습니다. 적용 구간과 실제 요청 내용을 확인하는 방법은 [문맥 자료](context-runtime.md)에 있습니다.

### Windows 실행 환경 진단

Ollama와 진단용 외부 프로그램을 시작할 때 GUI 패키지의 DLL 검색 디렉터리를 분리합니다. 자식 프로세스를 생성하는 동안만 Windows DLL 검색 디렉터리를 초기화하고 즉시 복원하며, 동시 실행은 공통 잠금으로 보호합니다. GUI `_internal`에 속한 PATH 항목도 자식 환경에서 제외합니다. Ollama의 자식 PATH에는 자체 배포본의 C++ DLL 디렉터리를 추가해 시스템에 해당 라이브러리가 없는 포터블 환경에서도 사용할 수 있게 합니다. Windows가 시스템 DLL을 선택할 수도 있으므로 실제 로딩 경로와 버전을 기록합니다. 별도 전역 라이브러리 설치는 필요하지 않습니다. 모델·병렬 처리·KV 캐시·CUDA 연산 설정은 변경하지 않습니다.

기존 서버 로그의 `RPT native launch`와 `data/errors/runtime-recovery.jsonl`의 `server_ready`에는 DLL 분리 정책, 부모 실행 파일, 원래 검색 디렉터리, 제거한 PATH 항목과 서버 작업 디렉터리를 기록합니다. 서버가 실행 중일 때 새 모델 실행 프로세스마다 `runtime_modules`를 기록하며, 실제 DLL 경로·버전과 GUI 패키지 DLL이 섞였는지를 확인합니다. 게임 파일이나 다른 프로그램의 모듈을 조사하지 않습니다.

오류 시 `runtime-<사건 ID>.json`에 분리 정보(`settings._runtime_launch`), 충돌 전 DLL 목록(`module_history`), 오류 시점 DLL 목록(`modules_at_failure`), Windows 충돌 보고 이벤트(`windows_crash_reports`)를 함께 보관합니다. `translation-requests.jsonl`에는 서버 PID와 서버 로그 경로를 추가해 해당 요청과 실행을 연결합니다. DLL 또는 Windows 기록을 읽지 못해도 수집 오류를 남기고 기존 복구를 이어갑니다. 이 수정만으로 GPU 오류의 근본 원인이 해결됐다고 판단하지 않습니다.

요청과 오류 기록은 실행 ID·서버 세대·요청 ID로 연결됩니다. 요청 시작·종료 시각과 경과 시간, 당시 동시 요청 목록을 함께 기록합니다. 실행 파일 식별값과 모델 digest도 남기며, 모델 파일 전체를 다시 해시하지 않습니다. 서버 정리 후 마지막 로그와 종료 결과를 보충해 실행기 자체 충돌과 프로그램의 강제 정리를 구분하고, 대기 중 뒤늦게 도착한 Windows 충돌 기록도 확인합니다. 같은 GPU 사고로 두 요청이 실패한 경우를 서로 독립적인 원인으로 단정하지 않습니다.

전처리 정보는 별도 필드에 저장하며, 이전 버전의 `Visual-stripped:` 오류 기록도 계속 복구할 수 있습니다.

### 마지막 수단으로 사용하는 CUDA 진단

일반 로그로 원인을 좁히지 못했을 때만 짧은 재현 시험에 사용합니다. 프로그램을 완전히 종료한 뒤 다음처럼 실행하면 해당 서버에만 CUDA 동기 진단을 적용합니다.

```powershell
$env:RPT_CUDA_DIAGNOSTICS = 'blocking'
& 'E:\Tools\RenPyTranslator\RenPyTranslator.exe'
Remove-Item Env:RPT_CUDA_DIAGNOSTICS
```

다음에 켜는 프로그램은 일반 모드로 돌아갑니다. 이 모드는 느려질 수 있어 서버 준비 시간은 60초, 요청 제한 시간은 30분으로 별도 적용합니다. 모델·컨텍스트·worker 설정은 그대로 유지합니다. 진단 모드는 로그의 `cuda_diagnostic_mode`로 확인할 수 있습니다.

추가로 NVIDIA Compute Sanitizer가 필요한 경우에는 별도 설치와 제한된 재현 시험을 준비해야 합니다. 프로그램이 자동 설치하거나 번역 중 몰래 실행하지 않습니다. CUDA 동기 진단과 Sanitizer를 동시에 켜서 비교 조건을 섞지 않습니다. 잘못된 메모리 접근 위치가 일반 로그의 동기화 함수와 같다고 단정할 수는 없습니다. [NVIDIA CUDA 설명](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/asynchronous-execution.html), [Compute Sanitizer](https://docs.nvidia.com/compute-sanitizer/ComputeSanitizer/index.html).

---

## English

### When a job fails

**Copy error details** copies a Markdown summary ready to paste into a bug report. **Save diagnostic file** exports more detailed technical information as JSON. Both include error, stage, application/runtime version and selected settings information, excluding dialogue, translations, saves, prompts and personal paths.

**Open log folder** opens the relevant error record's folder, or the common `logs` folder when no record is available. Original detailed logs can include game text and paths; use the exported diagnostic file when reporting an issue. Raw local output is available in the collapsed details.

RPG Maker plugin-text candidate counts and preserved identifier counts are informational. A candidate count is not a confirmed count of untranslated dialogue. Unreadable extra data is reported separately with a file count and local record.

Run translation again on the same project to reuse saved entries. An unsaved in-flight batch may repeat. Changing model settings or TXT context does not delete completed translations.

### Locations

| Location | Contents |
| --- | --- |
| `project/<game>-kr` | Installable patch and result ZIP |
| `data/projects/<game>` | Internal workspace and translation cache |
| Internal `data/translations.jsonl` | Saved translations |
| `data/failed-items.json` | Entries kept in the original language after failure |
| Internal `output/failed-repair-report.json` | Results of the latest Ren’Py recovery attempt |
| `data/translation-requests.jsonl` | Requests, responses, settings and validation |
| `data/context-report.json`, `context-history.jsonl` | TXT matching report and loading history |
| `data/rpg-context-scopes.json` | RPG Maker conversation classification |
| `data/rpg-source-language.json` | RPG Maker source-locale tables, binding counts and missing or ambiguous mappings |
| `data/rpg-extraction-notices.json` | RPG Maker extra-data read failures |
| `data/errors` | Project errors and server-recovery records |
| Application `logs` | Desktop, local server and GPU diagnostics |
| `logs/errors` | Errors before a project has been resolved |
| `logs/desktop-helper-*.log` | Helper failures when no detailed error file is available, including folder and settings queries |
| Application `logs/reports` | Default location in the diagnostic-file save dialog; another location can be selected |

Unless stated otherwise, `data/...` means the internal project's data directory. Failure lists and repair reports record different stages, so their counts can differ. New workspaces copy processing inputs; media remains at the source location. Existing workspaces are not automatically pruned.

### Log retention

Translation, retranslation and game updates remove diagnostic logs last modified at least seven days ago throughout the application folder, including other projects. Translation caches, failure lists, models, game files, saves and release ZIPs are retained. Busy logs are skipped.

Original and patched test copies are removed when a patch check finishes, fails or is cancelled. Reports and diagnostic logs remain under `data/validation-runs/<id>` and follow the same log retention period.

### Recorded details

Error JSON includes operation, file/item identifiers, model/runtime settings, exception, traceback, validation reasons and resume guidance. Request logs contain actual prompts and responses, including game text and paths. Failed diagnostic collection is recorded without blocking otherwise possible recovery.

The GUI shows a concise error message and error ID. Full tracebacks remain in the original error JSON; collapsed details show the job's console output. A failure to export a diagnostic file retains the original issue record.

Folder and settings query failures use the same reporting controls. An existing detailed error file is reused rather than creating another report for the same failure.

Recoverable HTTP 500/502/503/504, disconnections and timeouts trigger a restart of the tool-owned server with the same settings. Completed batches are retained and only unsaved items are requested again. Up to three restarts per incident wait **10 seconds, 30 seconds and 3 minutes**. A successful request from a restarted server resets the allowance for a later incident; there is no run-wide restart cap. Restarts are serialized. Cancellation, failure to stop the previous server or remaining request threads prevent a restart. Output-validation retries have a separate allowance.

The GUI distinguishes recovery in progress from an error that stopped the job, and displays the retry countdown and attempt number. Once progress resumes, transient errors remain available in the detailed record. Original-language fallbacks are counted separately from successful translations.

Runtime incidents are recorded in `data/errors/runtime-<incident ID>.json`, a corresponding `.server.log` and `runtime-recovery.jsonl`. They include the HTTP body, linked request, runtime options, server logs, recent GPU/RAM/process-memory samples, Windows error events and recovery outcome. Up to 150 recent resource samples are retained for incident reporting. Recovery does not establish or fix the underlying CUDA cause.

GPU records include total dedicated capacity, chosen profile, requested and actual model, memory estimates and TXT policy. TXT exclusion is recorded with `enabled:false`. See [Context](context-runtime.md) to distinguish loaded files from guidance actually used in a request.

Windows native-process launches isolate the GUI's DLL search directory and PATH entries, retaining the bundled runtime's own C++ libraries. Server-ready/module records and incident files capture launch policy, module paths/versions, module history and Windows crash reports when available. Request logs link server PID and log path. Missing diagnostics are recorded; DLL isolation is not proof that a GPU fault has been resolved.

Run IDs, server generations, request IDs and monotonic timestamps link concurrent requests to the affected server. Incident reports include recent request states, executable identities and the existing model digest. Final server logs and termination metadata distinguish a process crash from the application's cleanup, and delayed Windows events are collected after teardown. Model files are not rehashed.

`output_limit` identifies a response cut off by its output allowance; `missing_response_item` identifies an absent item in a response that finished normally. Long source entries reserve output space before generation and, when necessary, split at complete sentence or paragraph boundaries. A truncated response retains complete preceding items and replans only unfinished work. Request logs record these adjustments, and the completed translation keeps its original entry ID. Input preprocessing is stored separately from the error message. Legacy `Visual-stripped:` failure records remain recoverable.

### Last-resort CUDA diagnostics

Close the application before setting `RPT_CUDA_DIAGNOSTICS=blocking` in PowerShell, launch the EXE, then remove that variable from the shell as shown above. Only that launched application enables child-server `CUDA_LAUNCH_BLOCKING=1`; normal launches remain unchanged. This mode may be slow, so readiness allows 60 seconds and each request allows 30 minutes. Model, context and worker settings stay unchanged. Logs identify `cuda_diagnostic_mode`.

Compute Sanitizer is a separate, explicitly arranged reproduction step requiring its own installation. It is never installed or run automatically. Do not combine it with the blocking trial. An error reported at synchronization does not prove that synchronization caused it. See [NVIDIA CUDA](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/asynchronous-execution.html) and [Compute Sanitizer](https://docs.nvidia.com/compute-sanitizer/ComputeSanitizer/index.html).
