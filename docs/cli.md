# PowerShell CLI

[README](../README.md) · [사용 안내 / Usage](usage.md) · [오류와 로그 / Diagnostics](diagnostics.md)

## 한국어

### 기본 사용

아래 예시는 어느 PowerShell 작업 폴더에서든 실행할 수 있습니다. `$tool`을 압축을 푼 프로그램 경로로 지정합니다.

```powershell
$tool = 'E:\Tools\RenPyTranslator'
& "$tool\translate.ps1" -Help
& "$tool\translate.ps1" -Command run -Source 'D:\Games\YourGame' -SourceLanguage japanese
```

원문이 영어라면 `-SourceLanguage english`를 사용합니다. 새 프로젝트에서 생략하면 영어이며, 기존 프로젝트는 저장된 원문 언어를 유지합니다. 출력은 한국어입니다. 원문 언어 변경만으로 완료된 항목을 다시 번역하지 않습니다.

다국어 RPG Maker 게임에 인식 가능한 CSV·JSON 번역 테이블이 있으면 선택한 언어의 표시문구를 번역 입력으로 사용합니다. 테이블 형식을 확인할 수 없으면 플러그인의 동작을 추측해 치환하지 않습니다. 연결 결과는 내부 프로젝트의 `data/rpg-source-language.json`에서 확인할 수 있습니다.

명령 없이 실행하거나 `repair`에서 `-Fix`, `tasks`에서 `-Tasks`를 생략하면 도움말만 표시합니다. `check`는 Ren’Py에서 별도 검사 환경의 게임 엔진을 실행하며, RPG Maker에서는 게임을 실행하지 않고 정적 검사합니다.

### 재개·재번역·결과 재생성

```powershell
& "$tool\translate.ps1" -Command status -Project "$tool\project\YourGame-kr"
& "$tool\translate.ps1" -Command run -Project "$tool\project\YourGame-kr"
& "$tool\translate.ps1" -Command retranslate -Project "$tool\project\YourGame-kr"
& "$tool\translate.ps1" -Command rebuild -Project "$tool\project\YourGame-kr"
```

| 명령 | 용도 |
| --- | --- |
| `status` | 진행 상황 확인 |
| `run` | 준비부터 패치 생성까지 실행. 기존 프로젝트에서는 저장된 번역을 재사용 |
| `retranslate` | 준비된 프로젝트를 별도로 재번역. 이전 결과는 유지 |
| `rebuild` | 저장된 번역으로 패치를 다시 생성. 전체 번역을 다시 요청하지 않음 |
| `update` | `-Project`의 기존 번역과 `-Source`의 새 게임 버전을 연결해 추가·변경분 번역 |
| `check` | Ren’Py 원본·패치 비교와 확인된 패치 오류 보정 / RPG Maker 정적 검사 |

`-Project`에는 내부 작업 폴더와 프로그램이 만든 결과 폴더를 사용할 수 있습니다. 비교용 재번역이 중단됐다면 같은 비교 프로젝트와 모델로 다시 실행합니다.

업데이트·검사의 명령 예시와 보고서 분류는 [게임 업데이트와 패치 검사](updates-and-checks.md)를 참고하세요.

### 필요한 부분만 보정

```powershell
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix font
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix failed
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix names
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix display -ReferenceDisplay hide
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix layout -TextboxScale default
```

| `-Fix` | 처리 내용 | 지원 엔진 |
| --- | --- | --- |
| `font` | 글꼴·기본 메뉴·표시 | 두 엔진 |
| `failed` | 실패·미번역 후보 복구. 저장된 응답을 먼저 사용하고 남은 대상만 로컬 번역 | 두 엔진 |
| `display` | 원문 참고 표시·배치 | 두 엔진 |
| `layout` | 대화창 높이 조정 | 두 엔진 |
| `names` | 인명·호칭·입력 문구 및 관련 텍스트; RPG Maker는 인명 표기 통일 | 두 엔진 |
| `all` | Ren’Py는 전체 보정, RPG Maker는 폰트·메뉴·인명 표기와 미번역 복구 | 두 엔진 |

원문 참고를 다시 켜려면 `-ReferenceDisplay show`를 사용합니다. 높이는 `default` 또는 `1.2` 같은 배율로 지정합니다. 표시만 바꾸는 작업은 번역 캐시를 유지합니다.

### Ren’Py 추가 작업

```powershell
& "$tool\translate.ps1" -Command tasks -Project "$tool\project\YourGame-kr" -Tasks answers,routes
& "$tool\translate.ps1" -Command language -Project "$tool\project\YourGame-kr" -LanguageCorner right -LanguageMargin 12
```

`answers`<sup>beta</sup>는 입력 정답을 게임 안에 표시하고, `routes`<sup>beta</sup>는 선택지의 상태 변화와 후속 장면 힌트를 추가합니다. 누락된 장면 요약에는 로컬 모델을 사용할 수 있습니다. `language`는 언어 전환 패널만 적용합니다. 모든 동적 분기나 이미지 내용을 해석하는 기능은 아닙니다.

여러 작업을 함께 선택하려면 `tasks -Tasks`를 사용합니다. `run`·`retranslate`·`update` 중 하나만 지정할 수 있습니다. 기본 번역에는 일반 보정이 포함되므로 `run`에 보정 항목을 함께 적어도 별도 반복하지 않습니다.

### 공통 옵션

| 옵션 | 설명 |
| --- | --- |
| `-Source` | 처음 번역할 원본 게임 폴더 |
| `-Project` | 기존 내부 프로젝트 또는 번역 결과 폴더 |
| `-Model` | 사용할 모델 식별자 |
| `-RuntimeId` | 설치된 Ollama 실행 환경 식별자 |
| `-SourceLanguage` | `english` / `japanese` |
| `-Output` | 결과를 저장할 상위 폴더 |
| `-Suffix` | 결과 폴더 접미사. 기본 `-kr` |
| `-TextboxScale` | `default` 또는 높이 배율 |
| `-ReferenceDisplay` | 원문 참고 `show` / `hide` |

`verify-source`는 Ren’Py 원본 변경 확인에 사용합니다. RPG Maker 경로에서는 내부에 준비한 원문 사본의 해시를 확인합니다. `analyze`는 Ren’Py의 명시적인 사전 분석 작업으로 기본 번역에 포함되지 않으며 HY 같은 번역 전용 모델로 실행하지 않습니다. 나머지 단계별 명령은 `-Help`에서 확인할 수 있습니다.

RPG Maker MV/MZ는 `run`, `retranslate`, `update`, `check`, `prepare`, `status`, `rebuild`와 `font`·`names`·`failed`·`display`·`layout` 보정을 지원합니다. `names`는 번역된 캐릭터 DB의 표기를 화자 이름에 재사용하며 모델이 필요하지 않습니다. 정답·선택지 힌트·언어 패널은 지원하지 않습니다.

---

## English

### Start or resume

Set `$tool` to the extracted application folder. Commands can be run from any PowerShell working directory.

```powershell
$tool = 'E:\Tools\RenPyTranslator'
& "$tool\translate.ps1" -Help
& "$tool\translate.ps1" -Command run -Source 'D:\Games\YourGame' -SourceLanguage japanese
& "$tool\translate.ps1" -Command status -Project "$tool\project\YourGame-kr"
& "$tool\translate.ps1" -Command run -Project "$tool\project\YourGame-kr"
```

Use `english` for English source text. Omitting the option keeps an existing project's selection; a new project defaults to English. Output is Korean. Changing source language does not retranslate completed entries.

For multilingual RPG Maker games, recognized CSV/JSON translation tables supply the selected language's display text as translation input. Unknown plugin table formats are not guessed. Check `data/rpg-source-language.json` inside the workspace for the mapping results.

Running without a command, or omitting `-Fix` for `repair` or `-Tasks` for `tasks`, prints help only. The explicit `check` command runs Ren’Py in an isolated workspace; RPG Maker checks are static and do not launch the game.

`run` reuses saved translations. `retranslate` creates a separate comparison project and retains the old result. `rebuild` regenerates the patch from saved translations rather than translating the whole game again. `-Project` accepts an internal workspace or an exported result linked to it.

Use `update -Project <old-project> -Source <new-game>` to import a newer game, or `check -Project <project>` to inspect a patch. See [Game updates and patch checks](updates-and-checks.md).

### Repair an existing result

```powershell
& "$tool\translate.ps1" -Command retranslate -Project "$tool\project\YourGame-kr"
& "$tool\translate.ps1" -Command rebuild -Project "$tool\project\YourGame-kr"
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix font
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix failed
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix names
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix display -ReferenceDisplay hide
& "$tool\translate.ps1" -Command repair -Project "$tool\project\YourGame-kr" -Fix layout -TextboxScale default
```

| Scope | Purpose | Engines |
| --- | --- | --- |
| `font` | Fonts, standard menus and presentation | Both |
| `failed` | Failed or detected untranslated items; saved responses first, local inference only where needed | Both |
| `display` | Original-reference visibility and placement | Both |
| `layout` | Textbox height | Both |
| `names` | Names, address, inputs and affected text; RPG Maker actor-name consistency | Both engines |
| `all` | All Ren’Py corrections; fonts/menus, actor-name consistency and failed items for RPG Maker | Both |

Use `-ReferenceDisplay show` to restore references. Heights accept `default` or a multiplier such as `1.2`. Display-only changes retain the translation cache.

### Ren’Py-only tasks

```powershell
& "$tool\translate.ps1" -Command tasks -Project "$tool\project\YourGame-kr" -Tasks answers,routes
& "$tool\translate.ps1" -Command language -Project "$tool\project\YourGame-kr" -LanguageCorner right -LanguageMargin 12
```

Answer prompts appear in-game. Choice hints describe supported state changes and subsequent scenes; missing summaries may use local inference. They do not interpret every dynamic branch or image. The `language` command applies only the language panel.

Use `tasks -Tasks` to combine operations. Choose one of `run`, `retranslate` and `update`. Normal translation already includes its standard corrections.

### Options and engine support

Common options are `-Source`, `-Project`, `-Model`, `-RuntimeId`, `-SourceLanguage`, `-Output`, `-Suffix`, `-TextboxScale` and `-ReferenceDisplay`. The default suffix is `-kr`. GPU inference settings saved in the GUI also apply to CLI jobs; see [Models](models.md).

`verify-source` checks original-source changes for Ren’Py and prepared snapshot hashes for RPG Maker. `analyze` is an optional Ren’Py analysis operation requiring an appropriate general-purpose model, rather than the default HY translation-only model. See `-Help` for other pipeline commands.

RPG Maker MV/MZ supports `run`, `retranslate`, `update`, `check`, `prepare`, `status`, `rebuild`, and `font`/`names`/`failed`/`display`/`layout` repairs. `names` reuses translated actor database spellings in speaker labels without a model. Answer prompts, route hints and the language panel are not supported for RPG Maker.
