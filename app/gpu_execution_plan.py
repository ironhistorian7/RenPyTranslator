"""Per-device execution plans; Vulkan indices require runner identity verification."""
import os

from gpu_policy import validate_inference


def candidate_devices(devices, preferences, environment, strict=True):
    mode = preferences.get('gpu_mode', 'auto')
    wanted = set(preferences.get('gpu_ids', []))
    if mode == 'selected' and strict:
        missing = wanted - {d.get('id') for d in devices if d.get('selectable')}
        if missing:
            raise ValueError('선택한 GPU를 사용할 수 없습니다. 장치 일치 및 전용 VRAM을 확인하세요: ' + ', '.join(sorted(missing)))
    candidates = []
    for d in devices:
        ident = str(d.get('id', ''))
        if mode == 'selected' and ident not in wanted: continue
        cuda = ident.startswith('GPU-') and d.get('selectable')
        amd_backend=d.get('backend')
        amd = (d.get('vendor') == 'amd' and ident.startswith('DXGI-') and d.get('selectable')
               and amd_backend in ('vulkan','rocm') and type(d.get(amd_backend+'_index')) is int)
        if not (cuda or amd):
            if strict and d.get('vendor') == 'amd':
                print('GPU warning: AMD 장치 식별이 불가능하여 제외: ' + str(d.get('name', ident)), flush=True)
            continue
        total = d.get('total')
        if not isinstance(total, (int, float)) or total < 5888:
            if mode == 'selected' and strict: raise ValueError('선택한 GPU의 전용 VRAM이 6GB 미만이거나 확인되지 않았습니다: ' + ident)
            if strict: print('GPU warning: 전용 VRAM 6GB 미만 또는 확인 불가: ' + str(d.get('name', ident)), flush=True)
            continue
        if mode == 'auto':
            if cuda and 'CUDA_VISIBLE_DEVICES' in environment:
                visible = environment['CUDA_VISIBLE_DEVICES'].split(',')
                nvidia = [x for x in devices if str(x.get('id','')).startswith('GPU-')]
                position = next((i for i,x in enumerate(nvidia) if x.get('id') == ident), -1)
                if not any(v.strip() == str(position) or (v.strip().startswith('GPU-') and ident.startswith(v.strip())) for v in visible): continue
            if amd and (any(k in environment for k in ('HIP_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES','GGML_VK_VISIBLE_DEVICES','GPU_DEVICE_ORDINAL','OLLAMA_LLM_LIBRARY'))
                        or environment.get('OLLAMA_VULKAN','1').strip().lower() not in ('1','true')):
                if strict: print('GPU warning: 기존 GPU 환경 설정으로 AMD 자동 선택을 생략합니다: ' + ident, flush=True)
                continue
        candidates.append((d, cuda))
    if strict and mode == 'selected' and wanted - {d['id'] for d,_ in candidates}:
        raise ValueError('선택한 GPU를 실행 계획에 포함할 수 없습니다.')
    return candidates


def dual_mode(candidates, preferences):
    return preferences.get('gpu_mode','auto') in ('all','selected') and len(candidates)>=2


def require_manual_gpu_overrides(candidates, overrides):
    for device,_ in candidates:
        values=overrides.get(device['id'],{})
        name=str(device.get('name',device['id']))
        if not values.get('model'):
            raise ValueError(name+'의 모델을 선택해 주세요.')
        if 'parallel' not in values:
            raise ValueError(name+'의 동시 처리 수를 입력해 주세요.')


def device_plan(cfg, devices=None, preferences=None, environment=None, require_complete=True):
    from desktop_bridge import policy_devices, preferences as saved_preferences
    from gpu_policy import inference_preview
    preferences = saved_preferences() if preferences is None else preferences
    devices = policy_devices(cfg.get('runtime_id',preferences.get('runtime_id'))) if devices is None else devices
    environment = os.environ if environment is None else environment
    settings = validate_inference(preferences)
    candidates = candidate_devices(devices,preferences,environment)
    if not any(not cuda for _,cuda in candidates) and len(candidates)<=1: return []
    dual=dual_mode(candidates,preferences)
    if dual and require_complete:
        require_manual_gpu_overrides(candidates,settings['gpu_overrides'])
    preview=inference_preview(devices,preferences,environment)
    values_by_id={item['id']:item['effective'] for item in preview['devices']}
    result=[]
    for d,cuda in candidates:
        values=dict(values_by_id[d['id']])
        if cfg.get('_serial_model_request'): values['parallel']=1
        overrides=settings['gpu_overrides'].get(d['id'],{}) if dual else {}
        model=overrides.get('model')
        result.append(dict(id=d['id'],name=d.get('name',d['id']),vendor='nvidia' if cuda else 'amd',
                           backend='cuda' if cuda else d['backend'],selector=d['id'] if cuda else d[d['backend']+'_index'],
                           parallel=values['parallel'],config=dict({k:values[k] for k in ('num_ctx','kv_cache_type','context_txt_enabled')}, **({'model':model} if model else {})),
                           luid=d.get('luid'),vendor_id=d.get('vendor_id'),device_id=d.get('device_id'),
                           vulkan_index=d.get('vulkan_index'),pci_bus_id=d.get('pci_bus_id'),
                           total_mib=d['total'],estimated=not cuda,overrides=dict(overrides)))
    return result
