# KiriKiri / KAG

## 한국어

KiriKiri 게임 폴더 또는 게임이 들어 있는 ISO 폴더를 선택하면 엔진을 확인합니다. CLI에서는 ISO 파일을 직접 지정할 수도 있습니다. ISO를 마운트하거나 게임 실행 파일을 실행하지 않습니다.

현재 지원하는 시나리오는 KAG의 `.ks`와 KAGEX의 **암호화되지 않은 PSB v2 `.scn`**입니다. SCN은 `scenes` 아래의 표시 제목·대사·표시명·선택지 문구를 처리합니다. 내부 화자 ID, 음성 파일명, 분기 목적지와 상태 값은 유지합니다. 다른 언어별 중첩 구조, 암호화·MDF 압축·리소스가 포함된 SCN은 아직 지원하지 않습니다.

### 번역과 적용

1. 게임 폴더와 원문 언어, 모델을 선택합니다.
2. 이름 확인창에서 표시명을 확인한 뒤 번역합니다.
3. 완료 후 결과의 `APPLY.txt`를 확인합니다. 생성된 `patch*.xp3`를 **게임 실행 파일이 있는 폴더**에 복사합니다.

설치용 ISO 안의 게임은 먼저 평소 방법으로 설치하거나 압축을 풉니다. 번역 프로그램이 원본 ISO나 EXE를 수정하지는 않습니다. 패치는 같은 버전의 게임에 적용하고, 제거할 때는 프로그램이 만든 패치 파일만 삭제합니다. 기존 게임 패치는 삭제하지 마세요.

승인된 메뉴 이미지와 원본의 이름·해시·크기가 일치하면 한국어 이미지로 교체합니다. 일치하지 않는 이미지는 유지합니다. 일본어 로켈이 필요하다는 실행 비교 또는 사용자 확인이 있는 x86 게임에만 휴대용 런처가 생성됩니다. 생성된 경우 `APPLY.txt`에 따라 결과 파일과 `locale-runtime` 폴더를 게임 EXE가 있는 폴더에 복사하고 `Play-Korean.exe`로 실행합니다. 로켈 필요 여부가 불명확하거나 필요하지 않으면 런처를 만들지 않습니다.

지원하는 UI 자료에서는 확인 팝업, 저장·불러오기 버튼, 정렬 버튼과 장면 선택 탭도 교체합니다. 버튼의 기본·마우스 오버·선택 상태와 원래 크기·클릭 영역은 유지합니다. 장면 목록은 표시 제목만 번역하고 이동 ID와 Scene 번호를 보존합니다.

패치 내부 경로는 엔진의 부팅·스토리지 코드에서 확인한 검색 방식에 맞춥니다. 패치 루트만 검색하는 KAG/KAGEX에서는 파일을 루트에 배치하고, 패치 하위 폴더를 명시적으로 등록하는 경우에는 폴더 구조를 유지합니다. 검색 규칙을 확인하지 못하면 표준 KAG의 루트 배치를 사용하고 완료 경고에 남깁니다. 파일명이 대소문자만 다르거나 같은 이름으로 겹치면 덮어쓰지 않고 충돌을 표시합니다.

출력 기록에는 패치 경로와 원본 경로의 대응을 저장하며, 정적 검사도 이 대응과 확인된 검색 규칙을 검사합니다. 설치된 평면 패치를 다시 선택할 때는 고유한 원본 경로로 연결해 같은 스크립트가 중복 추출되지 않게 합니다. 기존 완성본의 경로만 고칠 때는 번역 캐시를 재처리하지 않고 기존 파일 내용 그대로 재포장하며, 교체 전 패치와 출력 기록을 백업합니다.

내부 작업 폴더에는 번역용 시나리오와 필요한 표시 코드·글꼴 설정만 보관합니다. 이미지·음악·영상·게임 실행 환경은 복사하지 않습니다. 번역 재개와 미번역 복구, 별도 재번역, 표시명 재검토, 정적 패치 검사를 사용할 수 있습니다. 게임 업데이트 병합·대화창 높이·실행 중 언어 전환·선택지 힌트는 이 경로에서 제공하지 않습니다.

KAGEX에서 표시명이 생략된 이름도 이름 확인창에 포함합니다. `charvoice.csv` 또는 `charvoice.txt`의 기본 별명은 정적으로 읽어 표시명을 확인하며, 실행식은 평가하지 않습니다. 내부 캐릭터 ID를 바꾸지 않고 표시명 칸에만 번역을 넣습니다. 비어 있는 표시명과 이름 없는 나레이션은 유지합니다.

기존 프로젝트는 다음 번역·미번역 복구·이름 보정 작업에서 누락된 이름 항목만 목록에 추가합니다. 기존 항목 ID와 대사 캐시는 유지하고, 확정한 이름 표기를 재사용합니다. 새 이름은 그룹별로 한 번만 자동 처리한 뒤 이름 확인창에 표시합니다. 원본을 찾을 수 없어 별명표를 읽지 못하면 내부 이름을 참고값으로 표시하고 직접 확인할 수 있습니다.

번역할 항목 주변의 원문은 같은 장면 안에서 앞 최대 8항목·뒤 최대 4항목, 추정 합계 2,000토큰까지 선택합니다. 문장 중간을 자르거나 큰 인접 문장을 건너뛰어 더 먼 문맥을 넣지 않습니다. 번역 대상 사이의 필수 문장과 TXT 지침은 이 주변 원문 제한과 별도로 유지하며, 전체 요청에는 기존 컨텍스트 창 검사를 적용합니다. 이 선택 방식은 HY와 범용 모델에 공통으로 적용됩니다.

`Context budget adjusted`는 실제 요청 창에 맞춘 조정이나 기존 예산 제한이 발생했을 때 표시합니다. 앞 8항목·뒤 4항목·2K 정책에 따른 정상적인 선별은 콘솔에 반복 표시하지 않고 요청 로그에만 남깁니다. 로그의 `surrounding_selection`은 사전 선택량을, `preselection_dropped`의 `policy_cap`은 문맥 정책 제한을, `dropped`의 `request_window`는 실제 요청 창에 맞추기 위한 추가 조정을 나타냅니다.

대사는 한국어만 표시합니다. KAGEX의 검색용 문자열은 한국어 본문을 기준으로 갱신합니다. 구형 엔진에서도 맑은 고딕을 찾을 수 있도록 한글·영문 글꼴 이름을 함께 지정합니다. 지원하는 사전 렌더 글꼴에서는 문자 인덱스를 확인하고, 한글이 없는 경우 한글의 폭 측정과 그리기에 같은 한글 글꼴을 사용합니다. 저장된 글꼴 선택값과 번역문은 유지합니다.

확인된 백로그·금칙 처리·직접 그리기 경로에도 같은 글꼴 처리를 적용합니다. 기본 이름을 사용하는 빈 표시 슬롯은 기존 프로젝트에도 추가하며, 같은 이름의 기존 승인 표기를 재사용합니다. 프로젝트의 `data/reviewed-terms.json`에 승인 용어를 기록하면 일본어 원문의 해당 용어가 있는 항목에만 표기 수정을 적용합니다. 번역 캐시 원본은 유지합니다.

보호표시의 구분자 오타는 원래 번호·개수·제어코드 검사를 유지하면서 정리합니다. KAGEX 번역문에 새로 생긴 `%`, `$`, `#`, `[`, `\`는 일반 문자로 표시하도록 이스케이프하며, 원문에서 보호한 기능 코드는 그대로 복원합니다.

일본어 번역에서는 감탄사·의성어·말버릇도 한국어로 옮깁니다. 잔류 문자를 한 글자씩 다시 보내지 않고 원문 문장으로 교정하며, 재시도 후에도 해결되지 않은 부분 번역은 마지막 확인창에 남깁니다. 해석 가능한 숫자만의 발화는 스크립트로 변환하고 수치를 비교합니다. 누락된 실제 줄바꿈도 단어를 자르지 않는 위치에 복원하며, 변수나 기능 제어 코드는 임의로 만들지 않습니다.

확인 가능한 KAGEX 루비는 본문과 독음으로 분리해 번역에 참고시키고 한국어 본문에 일본어 독음을 다시 붙이지 않습니다. 기존 자동 번역에 이런 루비가 남아 있으면 다음 재개 때 해당 항목만 다시 처리합니다. 저장된 정상 번역과 사용자가 확정한 수동 입력은 유지합니다.

### 오류와 검사

읽을 수 없는 형식, 손상된 컨테이너, 기존 패치와의 충돌은 작업을 중단하고 알림창과 오류 기록을 남깁니다. 선택 기능의 미적용 등 경고는 완료 후 목록에 모입니다. 오류 카드에서 **오류 내용 복사**, **진단 파일 저장**, **로그 폴더 열기**를 사용할 수 있습니다.

정적 검사는 출력 XP3의 구성과 시나리오 구조, 허용된 표시 위치 외의 제어 값 보존을 확인합니다. 실제 플레이·음성·화면 배치를 자동으로 검증하는 검사는 아닙니다. 실제 게임의 SCN 189개에 대해 읽기→쓰기→다시 읽기의 구조 동등성을 확인했으며, 게임 실행과 번역 모델 호출은 이 검증에 포함하지 않았습니다.

## English

Select a KiriKiri game directory or a directory containing its ISO. The CLI also accepts an ISO file directly. The adapter does not mount the image or launch the game.

Supported scenarios are KAG `.ks` and unencrypted PSB v2 `.scn` with the supported KAGEX `scenes` layout. Only display titles, dialogue, display names and choice captions are translated. Internal speaker identifiers, voice references, branch destinations and state values are preserved. Nested language variants, encrypted/MDF-compressed SCN and SCN with embedded resources are not supported yet.

Confirm names before translation, then follow `APPLY.txt` in the output folder. Copy the generated `patch*.xp3` beside the game's executable. Install or extract an installation ISO normally first. Original ISO/EXE files remain unchanged. Apply the patch to the same game version; uninstall by deleting only the generated patch.

Approved menu images are replaced with Korean images only when the source name, hash and dimensions match; unmatched images are left unchanged. A portable launcher is generated for x86 games only when a launch comparison or user confirmation establishes that Japanese locale is required. If generated, follow `APPLY.txt` to copy the output files and `locale-runtime` folder beside the game EXE, then run `Play-Korean.exe`. No launcher is generated when the need is unknown or absent.

Archive paths follow the patch search paths found in the engine's boot/storage code: root-only KAG/KAGEX loaders receive flat members; explicitly registered patch subdirectories retain their hierarchy. Unknown loaders use the standard KAG root layout with a completion warning. Case-insensitive filename collisions stop output rather than overwrite a member.

The output ledger maps archive members back to original paths, and static checks validate that mapping and confirmed loader rules. When a flat installed patch is selected again, unique source paths are retained without duplicate extraction. Correcting an existing output layout repackages its existing bytes without retranslating or changing the cache, and backs up the previous patch and ledger.

The workspace stores translation scenarios and required display code and font settings, without game media or runtime files. Resume, failed-item repair, separate retranslation, name review and static checks are available. Update merging, textbox resizing, runtime language switching and choice hints are unavailable for this engine.

KAGEX names with an omitted display value also appear in name review. Literal aliases from the engine's `charvoice.csv` or `charvoice.txt` are read without evaluating expressions. Translations fill only display-name slots; character IDs, intentionally empty labels and unnamed narration remain unchanged.

On the next translation, failed-item repair or name-correction task, existing projects append only missing name entries. Existing IDs and dialogue caches are retained, approved spellings are reused, and only new name groups receive automatic proposals. If the original alias table is unavailable, internal names remain available as references for manual review.

Surrounding source context is limited to up to eight preceding and four following entries in the same scene, with a combined estimated 2,000-token budget. Entries remain whole; an oversized neighbor ends selection in that direction. Mandatory entries between translation targets and supplied TXT guidance are retained separately, subject to the existing final request-window guard. HY and general-purpose models share this selection policy. Normal policy limits are recorded in the request log without a console notice. `Context budget adjusted` remains for actual request-window adjustments and other budget restrictions.

Dialogue is displayed in Korean only. Search text is derived from the Korean body. Default font selection includes both the Korean and English family names so legacy engines can find Windows Malgun Gothic. In recognized bitmap-font renderers, the tool checks the font character index and uses the same Korean font for Hangul measurement and drawing when that bitmap font has no Hangul. Saved font preferences and translated text remain intact.

Recognized backlog, line-breaking and direct UI drawing paths use the same font handling. Existing projects gain missing default-name display slots and reuse previously approved spellings for the same name. Project-specific `data/reviewed-terms.json` rules apply approved terminology only to entries containing the corresponding Japanese source term, preserving the original translation cache.

Supported UI assets also cover confirmation dialogs, save/load controls, sorting buttons and scene-selection tabs. Original dimensions, hit areas and normal/hover/selected states are retained. Scene-list translation changes display titles while preserving jump IDs and Scene numbers.

Response marker delimiters are normalized while retaining the existing identity, count and control-code checks. Newly generated KAGEX reserved characters (`%`, `$`, `#`, `[`, `\`) are escaped as display text before original protected controls are restored.

For Japanese sources, interjections, sound expressions and speech quirks are translated into Korean. Residual text is corrected using complete source utterances rather than isolated glyphs. Unresolved partial translations remain available for final manual review. Supported numeric-only utterances are converted and checked locally. Missing physical line breaks are restored at word boundaries; missing variables and functional controls are not invented.

Recognized KAGEX ruby annotations provide reading references without being reattached to Korean text. On resume, only affected legacy automatic translations are selected for an update; other saved translations and confirmed manual edits remain intact.

Fatal errors use the existing alert and diagnostic-report workflow. Nonfatal warnings are collected at completion. Static checks verify the native archive and preserve scenario control fields; they do not simulate play or prove visual/voice compatibility. Read/write structural round trips passed for 189 SCN files from the reference game without launching the game or model.

## 형식 참고 / Format references

- [KAG tags](https://krkrz.github.io/krkr2doc/kag3doc/contents/Tags.html)
- [KAG patch distribution](https://krkrz.github.io/krkr2doc/kag3doc/contents/Distribute.html)
- [emote-psb-rs](https://github.com/storycraft/emote-psb-rs) — PSB format reference, MIT. The Python adapter is implemented in this repository; no game scripts or binaries are bundled.
