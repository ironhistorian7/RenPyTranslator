# 모델과 GPU 설정 / Models and GPU settings

[README](../README.md) · [사용 안내 / Usage](usage.md) · [문맥 자료 / Context](context-runtime.md)

## 한국어

### 모델 준비

**AI 모델**에서 원하는 모델과 파일을 골라 다운로드합니다. 설치가 끝나면 사용할 모델을 선택하고 **적용**을 누릅니다. FULL에는 HY-MT2-7B Q6_K가 포함되고 LIGHT에는 모델이 없습니다.

직접 받은 단일 GGUF 파일은 프로그램의 `models` 아래 어느 하위 폴더에든 넣을 수 있습니다. **목록 새로고침**을 누르면 검색합니다. 앱에서 다운로드한 모델은 `models/ollama/blobs`에 보관하며, 저장된 파일명이 원래 GGUF 이름과 다를 수 있습니다.

설치된 모델에는 설치 상태가 표시되며 같은 파일의 중복 다운로드를 막습니다. **모델 폴더 열기**와 **삭제**도 이 탭에서 사용할 수 있습니다. 모델을 선택하지 않은 상태로 둘 수 있지만, 번역을 시작하려면 사용할 모델을 적용해야 합니다.

프로그램은 내장 Ollama를 자체 포트와 모델 저장소로 실행합니다. 별도로 설치한 Ollama의 모델 목록에 의존하지 않습니다. 다른 PC로 옮길 때는 프로그램 폴더 전체를 복사합니다.

Ollama의 구형·신형 모델 등록 형식을 함께 인식합니다. 이미 받은 모델이 등록 단계에서 실패했다면 설치 목록을 다시 확인하거나 같은 다운로드 작업을 재시도할 수 있습니다. 받은 파일이 정상인 경우 등록에 재사용합니다. 선택한 구버전 Ollama가 신형 등록 형식을 읽지 못하면 실행 환경을 바꿀 수 있도록 안내합니다.

### GPU 기본 설정

작업 시작 시 감지한 **총 전용 VRAM**으로 프리셋을 선택합니다. 남은 VRAM이나 순간 사용률로 설정을 바꾸지는 않습니다.

| GPU 용량 | HY 기준 모델 | 컨텍스트 창 | 동시 처리 | KV 캐시 | TXT 문맥 | 검증 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| 6GB급 | Q4_K_M | 4,096 | 2 | q8_0 | 제외 | 12GB 카드의 메모리 예산 시험 |
| 8GB급 | Q4_K_M | 8,192 | 2 | q8_0 | 제외 | 12GB 카드의 메모리 예산 시험 |
| 12GB급 | Q6_K | 16,384 | 2 | q8_0 | 사용 | RTX 4070 SUPER 12GB 기준 |
| 16GB급 | Q6_K | 16,384 | 3 | q8_0 | 사용 | 추정 |
| 24GB급 | Q6_K | 16,384 | 4 | q8_0 | 사용 | 추정 |
| 32GB급 이상 | Q6_K | 16,384 | 6 | q8_0 | 사용 | 추정 |

12GB 기본 설정은 유지하며, 상위 GPU에서도 컨텍스트 창을 자동으로 늘리지 않습니다. 중간 용량은 아래 등급을 사용하고, 용량 보고의 작은 차이는 허용합니다. 6·8GB는 실제 해당 카드에서 검증한 결과가 아닙니다. 4GB 이하는 지원 목표에 포함하지 않습니다.

NVIDIA는 CUDA를 사용합니다. 전용 VRAM 6GB 이상인 AMD GPU는 번들 ROCm에서 확인되면 ROCm을 먼저 사용합니다. ROCm이 장치를 인식하지 못하거나 모델을 올리지 못하면 확인된 Vulkan 경로로 전환합니다.

LIGHT와 FULL의 기본 Ollama 0.40.15에 공식 ROCm 런타임이 포함됩니다. 호환 Windows AMD 드라이버가 필요합니다. 내장 Ollama 0.34.2를 선택하면 AMD는 Vulkan을 사용합니다. 실제 실행 백엔드는 **설정 및 정보**와 작업 로그에서 확인할 수 있습니다.

6·8GB 설정은 TXT 문맥을 읽거나 전달하지 않지만 가까운 앞뒤 원문은 유지합니다. HY Q6를 선택했는데 해당 프로필에 맞지 않으면 같은 모델의 로컬 Q4를 찾습니다. 파일이 없으면 필요한 모델을 안내하며 자동 다운로드하지 않습니다. 다른 모델을 선택한 경우 HY로 바꾸지 않고 해당 GGUF의 구조로 메모리를 추정합니다.

### 고급 설정

**AI 모델 → 고급 설정**에서 프리셋·컨텍스트 창·동시 처리·KV 캐시·TXT 문맥 사용을 바꾸고 저장할 수 있습니다. **공통 설정 초기화**는 공통 지정값만 지우고 GPU별 설정은 유지합니다. 변경 사항은 다음 작업부터 적용되며, CLI도 같은 저장 설정을 사용합니다.

GPU를 두 개 이상 사용하면 **GPU별 설정**에서 각 장치의 모델과 동시 처리 수를 직접 지정합니다. 컨텍스트 창·KV 캐시·TXT 문맥도 장치별로 조정할 수 있습니다. 각 GPU의 초기화 버튼은 그 장치의 설정만 초기화합니다. 연결을 해제해도 장치별 설정은 보관됩니다.

6·8·12GB에서 시험한 값과 다르게 설정하면 안내가 표시됩니다. 12GB 초과에서는 값을 낮출 때만 경고하며 높일 때는 별도 경고를 표시하지 않습니다. 상위 GPU의 동시 처리 수는 추정값이므로 짧은 동일 구간의 완료 속도로 비교하면 됩니다. 컨텍스트 창을 키우는 것만으로 번역 품질이 좋아지지는 않습니다.

저장한 설정을 바꿔도 기존 완료 번역을 지우거나 다시 번역하지 않습니다. 결과를 비교하려면 [재번역](usage.md)을 사용합니다.

### GPU와 실행 상태 확인

**설정 및 정보**에서 모델 실행 상태와 GPU를 확인합니다. 자동 선택, 지원 GPU 모두 사용, 장치 직접 선택을 제공합니다. 여러 GPU를 사용하면 각 장치의 지정 모델이 별도 서버에서 실행되고 배치를 나눠 처리합니다. 모델은 각 GPU의 전용 VRAM에 들어가야 합니다.

GPU 사용량은 시스템 전체 수치이며, 번역에 사용 중 표시는 프로그램의 모델 프로세스가 확인된 장치에만 표시됩니다. 적용한 설정과 실제 모델 정보는 [작업 로그](diagnostics.md)에 남습니다.

---

### 추론 런타임

**설정 및 정보 → 추론 런타임**에서 Ollama 버전을 설치·선택할 수 있습니다. 버전별 실행 환경은 분리하고 모델 파일은 공유합니다. [자세한 사용법](updates-and-checks.md#추론-런타임-버전)을 참고하세요.

## English

### Prepare a model

In **AI Model**, select a repository and file to download. After installation, select the model and click **Apply**. FULL includes HY-MT2-7B Q6_K; LIGHT includes no model.

A manually downloaded single GGUF can be placed anywhere below `models`. Refresh the list to discover it. In-app downloads are stored in `models/ollama/blobs`, where filenames may differ from the original GGUF filename.

The tab shows installation status, prevents duplicate downloads, and provides folder opening and deletion. An empty selection is allowed, but translation requires an applied model. The bundled Ollama uses its own port and model store, independently of a system installation. Move the entire portable folder to keep models with the application.

Both legacy and newer Ollama registration formats are recognized. If a completed download previously failed during registration, check the installed list again or retry the same download; intact downloaded files are reused. If the selected older runtime cannot read a newer registration format, the application identifies the runtime incompatibility.

### GPU presets

Presets use **total dedicated VRAM** at job start, rather than currently free memory or live utilization.

| GPU class | HY model | Context | Workers | KV cache | TXT context | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 6GB | Q4_K_M | 4,096 | 2 | q8_0 | Off | Memory-budget tests on a 12GB card |
| 8GB | Q4_K_M | 8,192 | 2 | q8_0 | Off | Memory-budget tests on a 12GB card |
| 12GB | Q6_K | 16,384 | 2 | q8_0 | On | RTX 4070 SUPER 12GB baseline |
| 16GB | Q6_K | 16,384 | 3 | q8_0 | On | Estimate |
| 24GB | Q6_K | 16,384 | 4 | q8_0 | On | Estimate |
| 32GB+ | Q6_K | 16,384 | 6 | q8_0 | On | Estimate |

The 12GB baseline is retained. Larger GPUs do not automatically increase context. Intermediate capacities use the lower tier, with tolerance for small capacity-reporting differences. The 6/8GB profiles were not validated on actual 6/8GB cards. Cards with 4GB or less are outside the support target.

NVIDIA uses CUDA. An AMD GPU must be identifiable and have at least 6GB of dedicated VRAM. The app uses the bundled ROCm runtime when it recognizes the GPU and falls back to a verified Vulkan device when ROCm detection or model loading fails.

LIGHT and FULL include official ROCm with the default Ollama 0.40.15. A compatible Windows AMD driver is required. The bundled Ollama 0.34.2 uses Vulkan on AMD. See **Settings & Info** or the job log for the active backend.

Small-GPU profiles skip TXT files but retain nearby source text. If the selected HY Q6 does not fit, the tool looks for a local Q4 of the same model. If missing, it asks for the required file without downloading automatically. Other models use estimates from their own GGUF metadata and are not replaced by HY.

### Advanced settings

**AI Model → Advanced settings** allows saved overrides for preset, context, workers, KV cache and TXT context. **Reset common settings** clears common overrides while keeping per-GPU settings. Changes apply to the next job and are shared with the CLI.

When using two or more GPUs, select a model and worker count for each device under **Per-GPU settings**. You can also set context size, KV cache and TXT context per device. Each device has its own reset button. Settings are kept when a device is disconnected.

Values outside the tested settings show a notice on 6/8/12GB profiles. Above 12GB, reductions show warnings and increases do not. Larger-GPU worker counts are estimates; compare completion times on the same short passage. A larger context window alone does not guarantee better translation.

Changing settings does not discard completed translations. Use [retranslation](usage.md) for comparison.

### Device and runtime status

**Settings & Information** shows model status and GPU devices, with automatic, all-supported and explicit selection modes. Each GPU runs its selected model in a separate server and processes separate batches. The model must fit in that GPU's dedicated VRAM.

Utilization figures cover the whole system. A device is marked as used for translation only when the application's model process is detected on it. Applied settings and runtime information are recorded in the [logs](diagnostics.md).

### Inference runtimes

Install and select Ollama bundles under Settings and information → Inference runtime. Runtime files are separate per version; model files are shared. See [runtime instructions](updates-and-checks.md#runtime-versions).
