# 제3자 구성요소 고지 / Third-party notices

## 한국어

이 문서는 프로그램에 포함된 외부 구성요소의 출처와 라이선스를 안내합니다. RenPyTranslator 자체 코드의 라이선스는 [LICENSE](LICENSE)를 참고하세요.

### Gameloc

RPG Maker MV/MZ 지원에 **Gameloc 3.1.1**의 코드를 포함해 사용합니다.

- 원본 저장소: [eeewsm123123/gameloc](https://github.com/eeewsm123123/gameloc)
- 라이선스: **GPL-3.0-or-later**, [라이선스 전문](vendor/gameloc/LICENSE)
- 포함된 소스: `vendor/gameloc/gameloc`
- 버전·출처·파일별 SHA-256: [upstream.json](vendor/gameloc/upstream.json)
- 원본 제3자 고지: [THIRD-PARTY-NOTICES.txt](vendor/gameloc/THIRD-PARTY-NOTICES.txt)

엔진 감지, 대사·선택지·데이터 문자열 추출, 번역문을 원래 위치에 적용하는 처리, 제어문자 인식과 플러그인 목록 등록에 Gameloc 코드를 사용합니다. 연결 코드는 `app/rpgmaker_project.py`에 있으며, 추출 시 같은 문장도 위치별로 유지하도록 중복 합치기를 끕니다.

포함된 `treepath.py`·`jsonnest.py`에는 특수문자가 들어간 JSON 키를 처리하는 로컬 수정이 있습니다. 원본과 수정본의 해시는 `upstream.json`의 `local_changes`에 기록합니다.

`apply.py`는 원문에서 수집한 식별자 목록을 적용과 검사에 공유할 수 있도록 수정했습니다. 생성된 출력 플러그인을 다시 원문으로 판별하지 않도록 하기 위한 변경입니다.

`extract.py`는 파일 읽기 실패와 추출 범위 안내를 구조화된 콜백으로 전달하도록 수정했습니다. 프로그램에 없는 설정을 안내하지 않으며, 원본 명령행 출력은 콜백을 지정하지 않았을 때 유지됩니다.

`extract.py`·`apply.py`·`engines/rpgmaker.py`는 같은 문자열이라도 표시 위치와 기능용 위치를 구분하도록 수정했습니다. 표준 대사·선택지·메뉴·설명 필드의 구조를 확인하며, 기능 조회에 쓰일 수 있는 DB 이름·타입 이름·스크립트·플러그인 키는 기존 보호를 유지합니다.

로컬 AI 번역·GUI·캐시·재개·문맥 전달은 RenPyTranslator에서 처리합니다. 한국어와 원문을 함께 표시하는 플러그인은 `app/rpgmaker_bilingual.js`에 구현되어 있습니다. Gameloc 프로그램을 별도로 설치할 필요는 없으며, Ren’Py 번역에는 기존 RenPyTranslator 경로를 사용합니다.

다른 구성요소의 버전·라이선스는 아래 표에 정리되어 있습니다. 포함된 소스와 글꼴의 무결성 정보는 `vendor/asset-lock.json`, 출처 정보는 `vendor/versions.json`에 보관합니다.

### Locale Emulator

휴대용 일본어 로켈 런처는 [Locale Emulator v2.5.0.1](https://github.com/xupefei/Locale-Emulator/releases/tag/v2.5.0.1)의 DLL과 수정된 LEProc 래퍼를 사용합니다. 라이선스는 LGPL-3.0이며 포함된 `runtimes/locale`의 `COPYING`, `COPYING.LESSER`, `PROVENANCE.txt`, `SOURCE-NOTICE.txt`, 런처 소스와 상류 소스 압축파일을 참고하세요. Core 소스의 정확한 DLL 대응 커밋은 확인되지 않아 해당 압축파일만으로 완전한 대응 소스임을 입증하지 못합니다.

### Unity 에셋 처리

Windows Unity의 직렬화된 TextAsset은 [UnityPy](https://github.com/K0lb3/UnityPy) 1.25.3으로 읽고 저장합니다. UnityPy와 함께 배포하는 Python 의존성의 고지는 `licenses`에 들어 있습니다.

### KiriKiri 형식 참고

KAG 태그와 패치 적용은 [KiriKiri 공식 문서](https://krkrz.github.io/krkr2doc/kag3doc/contents/Tags.html)를 참고했습니다. PSB 읽기·쓰기 형식은 MIT 라이선스의 [storycraft/emote-psb-rs](https://github.com/storycraft/emote-psb-rs)를 참고해 Python으로 구현했습니다. 해당 Rust 라이브러리나 게임의 스크립트·실행 파일은 배포본에 포함하지 않습니다.

---

## English

These notices describe bundled third-party components, not a license grant for RenPyTranslator's own code.

### Gameloc

Gameloc 3.1.1 source code is bundled under `vendor/gameloc/gameloc` and used for RPG Maker MV/MZ engine detection, text extraction, applying translations at their original locations, control-code grammar and plugin registration.

- Upstream repository: [eeewsm123123/gameloc](https://github.com/eeewsm123123/gameloc)
- License: **GPL-3.0-or-later**, [full license](vendor/gameloc/LICENSE)
- Version, provenance and per-file SHA-256: [upstream.json](vendor/gameloc/upstream.json)
- Upstream notices: [THIRD-PARTY-NOTICES.txt](vendor/gameloc/THIRD-PARTY-NOTICES.txt)

The adapter is implemented in `app/rpgmaker_project.py`. Extraction disables deduplication to preserve separate text locations. Local AI translation, the GUI, caches, resume and context handling remain in RenPyTranslator; bilingual presentation is implemented in `app/rpgmaker_bilingual.js`. No separate Gameloc installation is required. Ren’Py uses the existing RenPyTranslator pipeline. Snapshot hashes are verified during setup and builds.

The bundled `treepath.py` and `jsonnest.py` contain local changes for literal JSON keys with special characters. Original and modified hashes are recorded under `local_changes` in `upstream.json`.

`apply.py` also accepts source identifiers shared by rendering and validation, so generated output plugins are not scanned as original game code.

`extract.py` adds an optional structured-notice callback for file-read failures and extraction-scope information. The adapter replaces instructions for settings absent from this application, while calls without the callback retain upstream CLI output.

`extract.py`, `apply.py` and `engines/rpgmaker.py` also distinguish displayed and functional occurrences of equal strings by standard message, choice, menu and description locations. Database/type names, scripts and plugin keys that may act as lookup values retain identifier protection.

### Locale Emulator

The portable Japanese-locale launcher uses DLLs from [Locale Emulator v2.5.0.1](https://github.com/xupefei/Locale-Emulator/releases/tag/v2.5.0.1) and an adapted LEProc wrapper under LGPL-3.0. See bundled `runtimes/locale` for `COPYING`, `COPYING.LESSER`, `PROVENANCE.txt`, `SOURCE-NOTICE.txt`, launcher source and upstream source archives. The exact Core commit corresponding to the released DLLs is unconfirmed; those archives do not establish complete corresponding source.

### Unity assets

[UnityPy](https://github.com/K0lb3/UnityPy) 1.25.3 reads and writes serialized TextAssets in Windows Unity builds. Notices for UnityPy and bundled Python dependencies are in `licenses`.

### KiriKiri format references

The KAG adapter follows the [official KiriKiri documentation](https://krkrz.github.io/krkr2doc/kag3doc/contents/Tags.html). The Python PSB reader/writer uses [storycraft/emote-psb-rs](https://github.com/storycraft/emote-psb-rs), licensed under MIT, as a format reference. Neither that Rust library nor game scripts or binaries are bundled.

### Bundled components

| Component | Pinned source / version | License and bundled notice |
| --- | --- | --- |
| unrpyc | 2.0.4, commit `3ae8334ed71a05535927dcc559663d3aca51215b` | MIT; `vendor/unrpyc/LICENSE` |
| Gameloc format adapters | 3.1.1 source snapshot, per-file SHA-256 in `vendor/gameloc/upstream.json` | GPL-3.0-or-later; vendored source, `vendor/gameloc/LICENSE` and `THIRD-PARTY-NOTICES.txt` |
| NanumSquareNeo Regular / Bold | NAVER files identified by SHA-256 in `vendor/fonts/nanum-sources.json` | SIL OFL 1.1; `vendor/fonts/NanumSquareNeo-OFL.txt` |
| Nanum Pen Script | Google Fonts file identified by SHA-256 in `vendor/fonts/nanum-sources.json` | SIL OFL 1.1; `vendor/fonts/NanumPen-OFL.txt` |
| Noto Sans CJK KR | Noto CJK file identified by SHA-256 in `vendor/fonts/sources.json` | SIL OFL 1.1; `vendor/fonts/OFL.txt` |
| Node.js (development only) | Version and official archive hash in `runtime-lock.json` | Node.js and dependency notices in the runtime `LICENSE` file |
| Ollama | Version and official archive hash in `runtime-lock.json` | MIT and backend/dependency notices retained from its official Windows archive |
| Electron / Chromium | `desktop/package-lock.json` | `LICENSE` and `LICENSES.chromium.html` in `_desktop` |
| CPython and Python packages | Python 3.12; `requirements*.txt` | Python/package notices copied into `licenses` by the portable builder |
| Hy-MT2-7B Q6_K (FULL only) | Official Tencent GGUF repository and immutable revision in `runtime-lock.json` | Apache 2.0; `licenses/Hy-MT2-LICENSE.txt`, pinned from the official 7B model repository |

Vendored source/font integrity is recorded in `vendor/asset-lock.json`; `vendor/versions.json` records provenance. The FULL model is redistributed unchanged. Other downloaded models retain their respective upstream licenses and are not included by this release builder.

Sources: [Gameloc](https://github.com/eeewsm123123/gameloc), [unrpyc](https://github.com/CensoredUsername/unrpyc), [NAVER fonts](https://hangeul.naver.com/font), [Google Fonts](https://github.com/google/fonts), [Noto CJK](https://github.com/notofonts/noto-cjk), [Node.js](https://nodejs.org/), [Ollama](https://github.com/ollama/ollama), [Electron](https://github.com/electron/electron), [official HY GGUF](https://huggingface.co/tencent/Hy-MT2-7B-GGUF).
