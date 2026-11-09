# 문맥 자료 / Context files

[README](../README.md) · [모델과 GPU 설정 / Models](models.md) · [오류와 로그 / Diagnostics](diagnostics.md)

## 한국어

### 문맥 자료 사용

TXT 문맥 자료는 배경·용어·현재 관계·말투를 번역 모델에 전달하는 보조 자료입니다. 폴더가 있으면 기본 번역에서 자동으로 사용하고, 없으면 문맥 자료 없이 진행합니다. 프로그램이 별도 AI로 전체 이야기를 분석하거나 Grok을 호출하지는 않습니다.

| 엔진 | 문맥 폴더 | 파일별 자료 예시 |
| --- | --- | --- |
| Ren’Py | `game/context` | `game/context/files/chapters/opening.rpy.txt` |
| RPG Maker, `data` 구조 | 게임 폴더의 `context` | `context/files/data/Map001.json.txt` |
| RPG Maker, `www/data` 구조 | `www/context` | `www/context/files/data/Map001.json.txt` |

파일별 경로는 Ren’Py의 `game` 또는 RPG Maker의 `data` 상위 폴더를 기준으로 맞춥니다. 원본 확장자를 뺀 `opening.txt`, `Map001.txt` 형태도 읽을 수 있습니다. 같은 자료를 여러 경로에 중복 저장할 필요는 없습니다.

원본 폴더가 남아 있으면 그곳의 문맥 자료를 읽습니다. 원본을 옮겼거나 보정만 따로 실행할 때는 내부 작업 공간에 준비된 자료를 사용합니다. 6·8GB 기본 프리셋에서는 TXT 자료를 제외하며, 이는 [모델 고급 설정](models.md)에서 확인할 수 있습니다.

### 파일 구성

모든 파일은 UTF-8 TXT로 저장합니다.

| 파일 | 내용 |
| --- | --- |
| `global.txt` | 게임 전체에 공통으로 필요한 배경과 문체 |
| `terms.txt` | 용어의 원문, 한국어 표기, 적용 의미·범위 |
| `files/<원본 상대경로>.txt` | 해당 파일이나 장면에 필요한 정보 |
| `characters.txt` | 전체 인물·식별자 보관용. 매 요청에 자동 전달하지 않음 |

`global.txt`는 다음처럼 주입할 내용과 근거를 나눌 수 있습니다.

```text
## 주입용 공통 지침
게임 전체에 적용되는 배경과 서술 문체를 적습니다.

## 관리용 근거
원문 근거와 확인이 필요한 사항을 적습니다.
```

`terms.txt`는 한 줄에 세 열을 사용합니다.

```text
원문 | 권장 한국어 | 적용 의미·범위
```

용어 표의 조건은 모델에 전달되는 설명입니다. 프로그램이 조건을 실행해 자동 치환하는 규칙은 아닙니다. 용어 표의 항목을 모두 인명으로 분류하지도 않습니다.

### 파일·장면 범위

파일 전체에 같은 배경이 적용된다면 파일별 TXT에 일반 문장으로 적을 수 있습니다. 관계나 말투가 중간에 바뀌면 범위를 나눕니다.

아래는 형식 예시입니다. 꺾쇠 안의 항목에는 실제 대본에서 확인한 label과 문장을 넣습니다.

```text
## Scene ID: scene_01
Applies to: label <실제_label> — 시작 "<실제 시작 문장>" ~ 끝 "<실제 끝 문장>"
Injection context:
화자: 이 구간의 실제 화자
청자: 확인된 상대 또는 미확인
현재 관계: 이 구간에서 확인되는 관계
말투: 이 화자가 해당 상대에게 사용하는 말투
Management:
판단 근거가 되는 원문과 불확실한 사항
```

한 label 전체는 `Applies to: label <실제_label> 전체`, 파일 전체는 `Applies to: whole file`로 지정할 수 있습니다. RPG Maker에는 Ren’Py label을 만들지 않습니다. 파일 전체 범위나 실제 추출 항목 ID, 해당 파일 안에서 구분되는 원문 시작·끝 문장을 사용합니다.

장면 안의 상태 변화는 서로 겹치지 않는 Scene ID로 나누거나 `State ID` 블록을 사용합니다. `Start ID`와 `End ID`에는 실제 카탈로그의 항목 ID를 넣습니다. 자연어 `Branch/Condition`만으로 실행 경로를 판정하지는 않습니다.

시작·끝 인용은 원문과 맞아야 합니다. 같은 문장이 반복되면 label 또는 항목 ID로 범위를 좁힙니다. 구간이 겹치거나 명확하지 않으면 해당 장면 문맥을 제외하고 공통·파일 배경·관련 용어를 사용하며, 이유를 보고서에 남깁니다. 지정한 label에 번역 항목이 없으면 `no_catalog_entries`로 기록합니다.

파일 전체 배경은 첫 Scene ID 앞의 `File overview / Injection context / Management` 블록에도 적을 수 있습니다. 관리용 근거는 번역 요청에 넣지 않습니다. `화자`, `청자`, `현재 관계`, `말투 A→B` 같은 명시적 필드는 현재 번역 대상 가까이에도 전달합니다. 이름 변수는 기존 기본 이름·분류 정보와 연결합니다.

외부 서비스로 자료를 준비할 때 복사해 쓸 문안은 [Grok 문맥 준비 프롬프트](grok-context-preparation.txt)에 있습니다.

### 컨텍스트 창과 분량

HY의 12GB 기본 컨텍스트 창은 16,384토큰입니다. TXT 자료의 4,500토큰은 작성 권장량이며 고정 상한이 아닙니다. 원문·번역 지시·출력 공간을 포함한 전체 요청 안에서 길이를 조절합니다. 파일 하나는 256,000바이트까지 읽으며, UTF-8이 아니거나 읽지 못한 파일은 이유를 기록합니다.

같은 런타임의 토크나이저를 사용할 수 있으면 최종 입력을 계산하고, 사용할 수 없으면 추정값임을 기록합니다. 요청이 창을 넘으면 배치를 먼저 나눕니다. 앞뒤 원문은 대사 항목 전체로 넣고, label·분기·이동·TXT 장면 경계를 넘기지 않습니다. 이미지 교체인 `scene` 명령만으로는 대화를 끊지 않습니다.

공간이 부족하면 먼 주변 대사부터 통째로 제외하고 TXT를 완전한 줄·용어 단위로 줄입니다. 가까운 원문과 현재 관계·말투를 우선하며, 번역 대상 문장 자체는 자르지 않습니다. 번역 대상만으로도 창을 넘으면 해당 항목과 예산을 오류로 기록합니다.

문맥이 길거나 자세하다고 말투·호칭·대명사 해석이 항상 정확해지는 것은 아닙니다. 자료는 모델의 판단을 돕지만 오역을 모두 막지는 못합니다.

### 이름표·UI와 대사 구분

이름표와 UI 라벨은 이야기 문맥·주변 대사를 제외한 요청으로 번역합니다. 해당 용어와 변수·인명 정보는 유지합니다. 짧다는 이유만으로 대사나 선택지를 이름표로 취급하지 않습니다.

이름표에 대사·설명이 붙는 등 잘못된 응답은 해당 항목만 재시도합니다. 정상 항목은 저장하고 재시도에도 실패한 항목은 원문으로 남깁니다. 이미 완료된 번역 전체를 다시 요청하거나 패치 생성을 중단하지는 않습니다.

### 실제 적용 확인

| 기록 | 확인할 내용 |
| --- | --- |
| `data/context-report.json` | 읽은 파일, 해시, 구간별 연결 결과와 누락 이유 |
| `data/context-history.jsonl` | 실행별 문맥 로딩 이력 |
| `data/translation-requests.jsonl` | 실제 프롬프트, 적용 범위, 토큰 예산, 제외한 자료와 이유 |
| `data/context-report.failed-repair.json` | 미번역 보정에서 사용한 문맥 기록 |

여기의 `data`는 내부 프로젝트의 폴더입니다. 파일을 읽었다는 기록과 특정 요청에 내용을 전달했다는 기록은 다릅니다. 보고서의 `scope_details`와 요청의 `translation_guidance`, 예산·제외 기록을 함께 보면 적용 여부를 확인할 수 있습니다. 배치 예산 분할은 `budget_split`으로 남습니다.

TXT를 수정해도 저장된 성공 번역은 자동 재번역하지 않습니다. 바뀐 문맥으로 결과를 비교하려면 **재번역**을 선택합니다. 과거 번역에 없던 문맥 해시나 요청 ID를 소급해서 만들지는 않습니다.

---

## English

### Locations and files

TXT context supplies background, terminology, relationships and register to the translation model. It is used automatically when present and enabled. No separate story-analysis model or external Grok call runs automatically.

| Engine layout | Context folder | Per-file example |
| --- | --- | --- |
| Ren’Py | `game/context` | `files/chapters/opening.rpy.txt` |
| RPG Maker with `data` | `context` beside `data` | `files/data/Map001.json.txt` |
| RPG Maker with `www/data` | `www/context` | `files/data/Map001.json.txt` |

Notes without the source extension, such as `opening.txt` or `Map001.txt`, are also supported. The original location takes precedence when available; standalone repairs or moved sources use the prepared workspace copy. The default 6/8GB profiles skip TXT context.

Use UTF-8 files: `global.txt` for shared guidance, `terms.txt` for `source | Korean | meaning/scope`, and `files/<source-relative-path>.txt` for file/scene notes. `characters.txt` is a reference document and is not automatically injected in full. Management/evidence sections are excluded from translation requests.

### Scope and relationships

Plain per-file notes apply throughout the file. Structured notes use `Scene ID`, `Applies to`, `Injection context` and `Management`. Scope can identify actual Ren’Py labels, exact source anchors, real `Start ID`/`End ID` entries or `whole file`. Do not use Ren’Py labels for RPG Maker; use file scope, actual catalog IDs or distinct anchors in the file.

Use non-overlapping scenes or `State ID` blocks for changes. Natural-language branch conditions are not executed. Ambiguous/overlapping scopes fall back to common/file guidance and terms, with recorded reasons. Labels with no catalog entries are reported as `no_catalog_entries`.

A `File overview` before the first scene can provide background throughout the file. Explicit speaker, listener, relationship and directional register fields are placed near the translation target. Terms are explanatory guidance, not an executable replacement dictionary or an automatic name list.

For external preparation, see the [Grok prompt](grok-context-preparation.txt).

### Token budget and request construction

The 12GB HY baseline uses a 16,384-token window. The suggested 4,500-token TXT size is a writing guideline, not a hard cap. Total request budgeting includes source, instructions and output space. Individual TXT files are limited to 256,000 bytes; unreadable or invalid UTF-8 files are reported.

Final inputs use the runtime tokenizer when available, otherwise a recorded estimate. Oversized batches are split before translation. Nearby source text is added as complete dialogue entries within label/branch/jump/context boundaries. A visual `scene` change alone does not end the conversation.

When space is short, distant source entries are removed whole and TXT is reduced by complete lines or terms. Nearby dialogue and current relationship/register are prioritized. The translation target is never cut mid-sentence; a target that cannot fit produces a budget error. Larger context does not guarantee correct register or address.

Speaker/UI labels are requested separately without story context or nearby dialogue, retaining relevant terms and name/variable metadata. Malformed label outputs retry only the affected entry and may fall back to the source without stopping all patch generation.

### Verify application

Inside the workspace, `data/context-report.json` records loaded files, hashes and scope matches. `context-history.jsonl` records each load. `translation-requests.jsonl` contains actual prompts, scope, budget and excluded guidance; `budget_split` records request splitting. Failed-item repair has its own `context-report.failed-repair.json`.

Loading a file does not mean every request received all of it. Compare `scope_details`, request `translation_guidance` and budget/exclusion records. Editing TXT does not invalidate successful cached translations. Use **Retranslation** for comparison; historical records are not assigned invented context hashes or request IDs.
