# 사용 안내 / Usage

[README](../README.md) · [모델 / Models](models.md) · [CLI](cli.md)

## 한국어

### 번역 시작

왼쪽 메뉴의 **번역**을 엽니다. 기본 작업은 번역이며, 일반적인 글꼴·메뉴·표시 보정도 함께 진행합니다.

#### 1. 게임 선택

게임 실행 파일이 있는 폴더를 선택하고 원문 언어를 **영어 / 일본어** 중에서 고릅니다. 기존 내부 프로젝트나 번역 결과 폴더도 같은 입력란에서 선택할 수 있습니다.

원문 언어 설정은 번역할 글의 언어를 모델에 알려줍니다. 다중언어 게임에서 다른 언어의 대본을 선택하거나 게임 자체의 언어를 바꾸는 기능은 아닙니다. 원본 대본과 `tl` 번역을 함께 가진 Ren’Py 게임에서는 선택한 언어의 대본이 실제 번역 대상으로 추출되는지 구분해야 합니다.

**기타 설정 · 실행할 작업**을 펼치면 재번역과 추가 작업을 선택할 수 있습니다. 기본 번역을 할 때는 보정 항목을 하나씩 추가할 필요가 없습니다.

#### 2. 모델 확인

사용할 로컬 모델과 감지한 GPU, 컨텍스트 창, 동시 처리 수, TXT 문맥 사용 여부를 확인합니다. 모델이 없다면 **AI 모델에서 다운로드·설정**으로 이동합니다. 모델을 준비한 뒤 선택하고 적용하면 됩니다.

#### 3. 출력 설정

결과 저장 위치를 정합니다. 기본값은 프로그램 폴더의 `project/<게임명>-kr`입니다. 재번역 결과는 비교할 수 있도록 별도의 이름으로 저장됩니다.

**기타 설정**에는 다음 항목이 있습니다.

| 설정 | 설명 |
| --- | --- |
| 대화창 높이 배율 | `default`는 게임 기본값, `1.2`는 기본 높이의 120% |
| 원문 참고 표시 | 한국어 아래에 원문을 55% 크기로 표시. 기본값은 켜짐 |
| 결과 폴더 접미사 | 기본값은 `-kr`. 이름이 겹치는 다른 결과에는 번호가 붙음 |
| 언어 전환 패널 | Ren’Py 전용. 화면 아래 모서리 위치와 여백 설정 |

원문 참고를 꺼도 번역에 실패해 원문으로 남은 문장은 계속 표시됩니다.

#### 4. 최종 확인

게임, 실행할 작업, 원문 언어, 모델과 결과 위치를 확인한 뒤 시작합니다. 진행 상황과 오류는 작업 기록에 표시됩니다. 도구는 원본 게임을 실행하지 않습니다.

### 완료와 중단

정상 완료되면 **결과 폴더 열기**로 패치를 확인합니다. **확인**을 누르면 현재 작업 화면을 초기화하고 첫 단계로 돌아갑니다. 저장된 번역·결과·모델은 그대로 남습니다.

실패하면 **로그 폴더 열기**가 표시됩니다. 폴더를 열면 첫 단계로 돌아가며, 열지 못하면 오류 화면을 유지합니다. 상세 오류 위치는 작업 기록에도 표시됩니다. 파일별 의미는 [오류와 로그](diagnostics.md)에 정리되어 있습니다.

취소하거나 오류가 나도 저장된 번역은 유지합니다. 같은 프로젝트를 선택해 **번역**을 다시 실행하면 완료한 항목을 재사용합니다. 취소 직전 저장되지 않은 배치는 다시 처리할 수 있습니다.

### 기존 결과 보정과 재번역

첫 단계에서 기존 프로젝트 또는 번역 결과 폴더를 선택하고 **기타 설정 · 실행할 작업**을 펼칩니다. **기존 번역 보정**에서 필요한 항목을 체크하면 번역 선택은 해제됩니다. 보정할 때마다 전체 번역을 다시 돌릴 필요는 없습니다.

| 작업 | Ren’Py | RPG Maker MV/MZ |
| --- | --- | --- |
| 재번역 | 지원 | 지원 |
| 폰트·메뉴 보정 | 지원 | 지원 |
| 미번역 복구 | 지원 | 지원 |
| 원문 참고 표시 보정 | 지원 | 지원 |
| 대화창·선택지 표시 조정 | 지원 | 지원 |
| 이름·호칭·입력 보정 | 지원 | 미지원 |
| 입력 정답 표시 | 지원 | 미지원 |
| 선택지 힌트 | 지원 | 미지원 |
| 언어 전환 패널 | 지원 | 미지원 |

지원하지 않는 항목은 화면에 표시되지 않습니다. 이름·미번역 보정과 누락된 장면 요약 등에는 로컬 모델을 사용할 수 있습니다. 글꼴이나 표시만 바꾸는 작업에는 번역 모델이 필요하지 않습니다.

**재번역**은 기존 결과를 남겨두고 별도 프로젝트에서 다시 번역합니다. 모델이나 TXT 문맥을 바꿔 비교할 때 사용합니다. 일반 번역 재개는 저장된 번역을 재사용하므로, 문맥 파일만 바꿔도 완료된 항목이 자동 재번역되지는 않습니다.

### 패치 적용과 호환 범위

Ren’Py는 결과의 `game`을 원본 게임의 `game`에 합칩니다. RPG Maker는 결과의 `APPLY.txt`를 따라 `data`·`js`·`fonts`를 같은 위치에 덮어씁니다. `www` 구조가 있으면 그대로 유지합니다. 적용 전 원본 파일을 보관하면 되돌리기 쉽습니다.

Ren’Py의 기본 메뉴와 퀵바에는 고정 번역을 사용합니다. 사전에 없는 게임 고유 커스텀 메뉴는 원문으로 남을 수 있습니다. 일반 글꼴은 나눔스퀘어네오, 손글씨는 나눔펜을 사용하고 아이콘용 글꼴은 보존합니다. 한국어 대사의 인명은 게임의 기본 이름을 기준으로 하며, 플레이어가 변경한 이름은 한국어 번역에 반영하지 않습니다. 별명·호칭 입력은 인명과 구분합니다.

Ren’Py 호환 목표는 6.99 계열, 7.3.5~7.8.7, 8.x입니다. 6.99.0·6.99.10·6.99.11·6.99.14.3 공식 SDK의 가상 대본으로 템플릿 생성과 패치 기능을 검사했습니다. RPG Maker는 MV/MZ를 지원합니다. 전용 메시지 플러그인·이미지로 된 UI·동적 대본은 게임별 확인이 필요할 수 있습니다. 다른 엔진과 구형 RPG Maker 형식은 번역 전에 안내하고 중지합니다.

---

### 게임 업데이트·패치 검사

기존 프로젝트를 선택하고 **기타 설정**에서 **게임 업데이트** 또는 **패치 검사**를 선택할 수 있습니다. 업데이트는 새 게임 폴더를 함께 지정합니다. 자세한 절차는 [게임 업데이트와 패치 검사](updates-and-checks.md)를 참고하세요.

## English

### Start a translation

Open **Translation** in the sidebar. The default translation job includes the normal font, menu and display corrections.

1. **Game:** Select the folder containing the executable, then choose English or Japanese. Existing workspaces and exported results use the same folder picker. The source-language setting tells the model how to translate; it does not select another built-in language script or change the game's own language. Expand **Other settings · Tasks** for retranslation or additional tasks.
2. **Model:** Check the local model, GPU, context window, concurrency and TXT context status. Use the link to **AI Model** to download, select and apply a model.
3. **Output:** Choose a destination. The default is `project/<game>-kr` below the application folder. **Other settings** contains textbox height, reference visibility and the output suffix. `default` keeps the original height; `1.2` means 120%. The original reference is on by default at 55% of the Korean text size. Ren’Py also offers language-panel placement. Retranslation produces a separate comparison result.
4. **Review:** Check the game, tasks, source language, model and destination, then start. Progress and errors appear in the job log. The tool does not launch the game.

Hiding the original reference does not hide untranslated fallback text.

### Completion, cancellation and errors

After success, **Open result folder** opens the patch location. **Confirm** resets the current workflow and returns to the first step, keeping saved translations, results and models.

Failed jobs offer **Open log folder**. A successful folder opening returns to the first step; if opening fails, the error page remains. The job log also links to detailed errors. See [Diagnostics](diagnostics.md).

Cancellation and errors retain saved translations. Select the same project and run **Translation** again to reuse completed entries. An in-flight unsaved batch may repeat.

### Repair or retranslate an existing result

Select an existing project or result in the first step and expand **Other settings · Tasks → Existing translation repairs**. Selecting a repair deselects the normal translation job. A full translation is not required for every correction.

| Operation | Ren’Py | RPG Maker MV/MZ |
| --- | --- | --- |
| Retranslation | Supported | Supported |
| Fonts and menus | Supported | Supported |
| Failed-item recovery | Supported | Supported |
| Original-reference display | Supported | Supported |
| Textbox and choice layout | Supported | Supported |
| Names, address and input | Supported | Not supported |
| Answer prompts | Supported | Not supported |
| Choice hints | Supported | Not supported |
| Language panel | Supported | Not supported |

Unsupported controls are hidden. Name/failure corrections and missing scene summaries may use the local model; font and display-only work does not need inference.

**Retranslation** keeps the old result and translates in a separate project for comparison. Normal resume reuses completed entries, even if the model settings or TXT context have changed.

### Apply and run

For Ren’Py, merge the output's `game` into the original `game`. For RPG Maker, follow `APPLY.txt` to replace `data`, `js` and `fonts` at the same locations, preserving `www` when present. Keeping the original files makes reverting easier. Run the game's normal executable afterward.

Ren’Py standard menus and quickbars use fixed translations. Custom menu text outside the dictionary may remain in its original language. Regular text uses NanumSquareNeo, handwriting uses Nanum Pen, and icon fonts are preserved. Korean dialogue uses default character names rather than player-renamed names; nicknames and forms of address are handled separately.

Ren’Py compatibility targets are 6.99, 7.3.5–7.8.7 and 8.x. Synthetic scripts were checked with official 6.99.0, 6.99.10, 6.99.11 and 6.99.14.3 SDKs. RPG Maker support covers MV/MZ. Custom message plugins, image-based UI and dynamic scripts may need separate checks. Other engines and legacy RPG Maker formats stop with an explanation before translation.

### Game updates and patch checks

Select an existing project and choose Game update or Patch check under Other settings. Updates also require the newer game folder. See [Game updates and patch checks](updates-and-checks.md).
