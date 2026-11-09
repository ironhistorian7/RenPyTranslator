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

로컬 AI 번역·GUI·캐시·재개·문맥 전달은 RenPyTranslator에서 처리합니다. 한국어와 원문을 함께 표시하는 플러그인은 `app/rpgmaker_bilingual.js`에 구현되어 있습니다. Gameloc 프로그램을 별도로 설치할 필요는 없으며, Ren’Py 번역에는 기존 RenPyTranslator 경로를 사용합니다.

다른 구성요소의 버전·라이선스는 아래 표에 정리되어 있습니다. 포함된 소스와 글꼴의 무결성 정보는 `vendor/asset-lock.json`, 출처 정보는 `vendor/versions.json`에 보관합니다.

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
