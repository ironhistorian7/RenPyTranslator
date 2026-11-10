# 개발 및 배포 / Development and distribution

[README](../README.md) · [CLI](cli.md)

## 한국어

소스를 수정하거나 배포본을 직접 만드는 개발자를 위한 안내입니다. 포터블 ZIP 사용자는 이 과정 없이 실행할 수 있습니다.

### 개발 환경 준비

소스 저장소에는 Python 3.12 x64가 필요합니다. Python이 없다면 `setup.ps1`이 설치 안내를 표시합니다. 실행용 라이브러리는 `requirements.txt`, 빌드 도구는 `requirements-build.txt`에 고정합니다. 전역 Python에 패키지를 설치하지 않습니다.

저장소 루트에서 실행하세요.

```powershell
.\setup.ps1
.\gui.ps1 -SourceMode
```

`setup.ps1`은 저장소의 `.venv`를 만들고 의존성을 설치하며, `runtime-lock.json`에 고정한 Node.js·Ollama를 `runtimes`에 다운로드합니다. SHA-256을 확인하고 `desktop/package-lock.json`으로 `npm ci`와 화면 빌드를 실행합니다. 기존 모델·사용자 설정·프로젝트·번역 캐시는 유지합니다. AI 모델은 이 단계에서 다운로드하지 않습니다.

Python을 직접 지정하려면:

```powershell
.\setup.ps1 -PythonExe 'C:\path\to\python.exe'
```

`.\gui.ps1`은 작업 폴더의 EXE가 있으면 이를 실행합니다. `-SourceMode`는 로컬 Electron과 수정한 소스를 실행합니다. CLI 래퍼는 소스와 `.venv`가 함께 있으면 소스를, 포터블 배포본에서는 EXE를 사용합니다. 시스템 PATH나 전역 Ollama 설정은 바꾸지 않습니다.

### 배포 빌드

```powershell
.\build.ps1                    # 기본: LIGHT, 모델 없이 빌드
.\build.ps1 -Edition Full      # 고정된 공식 HY Q6_K를 확인/준비하여 포함
.\build.ps1 -Edition Both      # 동일한 앱 빌드로 두 배포본 생성
.\build.ps1 -LocalOnly         # 작업 폴더 EXE만 갱신, 배포 ZIP 생성 안 함
```

배포 파일은 `runtime-lock.json`의 릴리즈 버전에 따라 `portable/V<버전>/`에 생성합니다. 빌드만으로 버전을 올리지는 않습니다. LIGHT는 크기에 따라 일반 ZIP 또는 분할 ZIP으로, FULL은 분할 ZIP으로 생성합니다. 분할 ZIP(`.zip`, `.z01`, `.z02` 등)의 각 조각은 1,900,000,000바이트 이하입니다. 분할 파일을 모두 같은 폴더에 받고 반디집으로 `.zip`을 열어 압축을 풉니다. 기존 동일 버전 배포 파일과 예전 `dist` ZIP은 `portable/backup/`에 옮겨 보존합니다. SHA-256 목록과 압축 해제 안내도 함께 생성합니다.

실행 폴더는 `dist/RenPyTranslator/`에 생성합니다. FULL에 필요한 HY가 없으면 고정된 공식 파일만 다운로드하고 해시를 검증합니다. 다른 모델이나 게임·설정·번역 캐시는 배포본에 넣지 않습니다. 큰 DLL과 EXE도 표준 ZIP Deflate로 압축하며, 기능이나 GPU 실행 라이브러리를 빼서 용량을 줄이지 않습니다. 분할 ZIP 빌드용 반디집과 7-Zip 도구는 고정 해시를 확인해 `build/tools`에만 준비하며 시스템에 설치하거나 배포본에 포함하지 않습니다.

일반 ZIP 빌드는 작업 폴더의 EXE와 앱 실행 폴더를 바꾸지 않습니다. 이를 갱신하려면 앱과 CLI를 모두 닫고 `-LocalOnly`를 명시하세요. 로컬 갱신은 실행 중인 앱과 잠긴 파일을 먼저 확인하고, 준비한 EXE·`_internal`·`_desktop` 전체를 교체하며 중간 오류 시 이전 구성요소를 되돌립니다. 모델·프로젝트·설정·캐시·기존 배포 ZIP·소스는 갱신 대상이 아닙니다. ZIP은 소스 레포에 넣지 말고 Releases에 게시하세요. `.gitignore`는 ZIP·`dist`·런타임·모델·캐시를 제외합니다. Git 저장소를 자동 생성하지 않습니다.

글꼴과 unrpyc 소스·라이선스는 `vendor`에 보관합니다. 버전·출처는 `vendor/versions.json`, 파일 해시는 `vendor/asset-lock.json`, 런타임과 FULL 모델 정보는 `runtime-lock.json`에서 관리합니다. README, `docs` 바로 아래의 Markdown·TXT 문서, 프로그램 라이선스와 [외부 구성요소 고지](../THIRD_PARTY_NOTICES.md)를 함께 배포합니다.

### 검증

```powershell
& '.\.venv\Scripts\python.exe' -B -X utf8 -m unittest discover -s app
& '.\RenPyTranslator-cli.exe' --self-check
```

자체 점검은 게임이나 모델 추론을 시작하지 않습니다. ZIP을 다른 폴더에 풀어 같은 점검을 수행하면 경로 의존성을 확인할 수 있습니다.

---

## English

This guide covers source development and release builds. Portable ZIP users do not need this setup.

### Prepare the development environment

Install Python 3.12 x64 for source development. If it is missing, `setup.ps1` prints installation instructions. Runtime libraries are pinned in `requirements.txt`; build tools are pinned in `requirements-build.txt`. Packages are installed only in the repository's `.venv`.

Run from the repository root:

```powershell
.\setup.ps1
.\gui.ps1 -SourceMode
```

Setup creates the local venv, installs dependencies, downloads the Node.js and Ollama versions locked in `runtime-lock.json` into `runtimes`, checks SHA-256, and runs `npm ci` plus the frontend build using `desktop/package-lock.json`. Existing models, settings, projects and translation caches are retained. Setup does not download an AI model.

To specify Python explicitly:

```powershell
.\setup.ps1 -PythonExe 'C:\path\to\python.exe'
```

`.\gui.ps1` runs the local EXE when available. `-SourceMode` runs repository sources with local Electron. The CLI wrapper uses source Python when both the source entrypoint and venv exist; otherwise it uses the portable CLI EXE. Setup does not change system PATH or global Ollama settings.

### Build a release

```powershell
.\build.ps1                    # Default: LIGHT; no model needed
.\build.ps1 -Edition Full      # Verify/prepare the pinned official HY Q6_K
.\build.ps1 -Edition Both      # Produce both editions from the same app build
.\build.ps1 -LocalOnly         # Update local EXEs without creating release ZIPs
```

Release assets are written to `portable/V<version>/`, using the release version in `runtime-lock.json`. Building does not increment it. LIGHT is a regular or split ZIP depending on its size. FULL is a standard split ZIP (`.zip`, `.z01`, `.z02`, etc.). Each split volume is at most 1,900,000,000 bytes. Existing assets for the same version and legacy ZIPs in `dist` are moved to `portable/backup/`. SHA-256 checksums and extraction instructions are included beside the archives. Download ALL volumes for the selected edition into the same folder, then open the `.zip` file with Bandizip.

The runnable bundle is generated in `dist/RenPyTranslator/`. FULL downloads only the pinned official HY file when missing and verifies its hash. Other models, games, personal settings and translation caches are excluded. Large DLLs and executables are compressed with standard ZIP Deflate; application features and GPU runtimes are retained. The pinned Bandizip and 7-Zip packaging tools are downloaded with hash verification into `build/tools` only; they are neither installed system-wide nor shipped in the distribution.

Normal ZIP builds leave the working-folder EXEs and application runtimes unchanged. To update them, close the app and CLI and explicitly use `-LocalOnly`. Local publishing checks for running apps and locked files before replacing complete EXE, `_internal` and `_desktop` components; a failed replacement restores the previous components. Models, projects, settings, caches, existing release ZIPs and sources are outside this update. Publish ZIPs in Releases rather than committing them. `.gitignore` excludes archives, `dist`, runtimes, models and caches. No Git repository is created automatically.

Fonts, unrpyc sources and licenses are vendored. Provenance is in `vendor/versions.json`, integrity hashes in `vendor/asset-lock.json`, and pinned runtime/FULL model assets in `runtime-lock.json`. The build includes README, top-level Markdown/TXT files in `docs`, the application license and [third-party notices](../THIRD_PARTY_NOTICES.md).

### Validation

```powershell
& '.\.venv\Scripts\python.exe' -B -X utf8 -m unittest discover -s app
& '.\RenPyTranslator-cli.exe' --self-check
```

Self-check starts neither a game nor model inference. Extract the ZIP to another folder and repeat it to check relocation.
