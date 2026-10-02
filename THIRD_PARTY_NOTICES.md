# Third-party assets

These notices describe bundled third-party components, not a license grant for RenPyTranslator's own code.

| Component | Pinned source / version | License and bundled notice |
| --- | --- | --- |
| unrpyc | 2.0.4, commit `3ae8334ed71a05535927dcc559663d3aca51215b` | MIT; `vendor/unrpyc/LICENSE` |
| NanumSquareNeo Regular / Bold | NAVER files identified by SHA-256 in `vendor/fonts/nanum-sources.json` | SIL OFL 1.1; `vendor/fonts/NanumSquareNeo-OFL.txt` |
| Nanum Pen Script | Google Fonts file identified by SHA-256 in `vendor/fonts/nanum-sources.json` | SIL OFL 1.1; `vendor/fonts/NanumPen-OFL.txt` |
| Noto Sans CJK KR | Noto CJK file identified by SHA-256 in `vendor/fonts/sources.json` | SIL OFL 1.1; `vendor/fonts/OFL.txt` |
| Node.js (development only) | Version and official archive hash in `runtime-lock.json` | Node.js and dependency notices in the runtime `LICENSE` file |
| Ollama | Version and official archive hash in `runtime-lock.json` | MIT and backend/dependency notices retained from its official Windows archive |
| Electron / Chromium | `desktop/package-lock.json` | `LICENSE` and `LICENSES.chromium.html` in `_desktop` |
| CPython and Python packages | Python 3.12; `requirements*.txt` | Python/package notices copied into `licenses` by the portable builder |
| Hy-MT2-7B Q6_K (FULL only) | Official Tencent GGUF repository and immutable revision in `runtime-lock.json` | Apache 2.0; `licenses/Hy-MT2-LICENSE.txt`, pinned from the official 7B model repository |

Vendored source/font integrity is recorded in `vendor/asset-lock.json`; `vendor/versions.json` records provenance. The FULL model is redistributed unchanged. Other downloaded models retain their respective upstream licenses and are not included by this release builder.

Sources: [unrpyc](https://github.com/CensoredUsername/unrpyc), [NAVER fonts](https://hangeul.naver.com/font), [Google Fonts](https://github.com/google/fonts), [Noto CJK](https://github.com/notofonts/noto-cjk), [Node.js](https://nodejs.org/), [Ollama](https://github.com/ollama/ollama), [Electron](https://github.com/electron/electron), [official HY GGUF](https://huggingface.co/tencent/Hy-MT2-7B-GGUF).
