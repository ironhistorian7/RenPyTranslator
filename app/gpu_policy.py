"""Job-local GPU policy from installed capacity and portable model metadata."""
import json
import math
import os
import re
from pathlib import Path

from app_paths import resource


def rules():
    return json.loads(resource('gpu_profiles.json').read_text(encoding='utf-8'))


FIELDS=('num_ctx','parallel','kv_cache_type','context_txt_enabled')


def validate_inference(preferences):
    preset=preferences.get('inference_preset','auto')
    if preset not in {'auto',*(p['id'] for p in rules()['profiles'])}:
        raise ValueError('번역 실행 프리셋을 확인하세요.')
    overrides=preferences.get('inference_overrides',{})
    if not isinstance(overrides,dict) or set(overrides)-set(FIELDS):
        raise ValueError('번역 고급 설정을 확인하세요.')
    overrides=dict(overrides)
    for key,low,high in [('num_ctx',512,262144),('parallel',1,32)]:
        if key in overrides and (type(overrides[key]) is not int or not low<=overrides[key]<=high):
            raise ValueError('컨텍스트는 512~262144, 동시 처리는 1~32 사이의 정수로 입력하세요.')
    if 'kv_cache_type' in overrides and overrides['kv_cache_type'] not in ('q8_0','q4_0','f16'):
        raise ValueError('KV 캐시 설정을 확인하세요.')
    if 'context_txt_enabled' in overrides and type(overrides['context_txt_enabled']) is not bool:
        raise ValueError('TXT 문맥 설정을 확인하세요.')
    per_gpu=preferences.get('gpu_overrides',{})
    if not isinstance(per_gpu,dict): raise ValueError('GPU별 설정을 확인하세요.')
    clean={}
    for ident,values in per_gpu.items():
        if not isinstance(ident,str) or not re.fullmatch(r'(?:GPU-[a-fA-F0-9-]+|DXGI-[a-fA-F0-9]{16})',ident):
            raise ValueError('GPU 장치 목록을 확인하세요.')
        if not isinstance(values,dict) or set(values)-{'parallel','kv_cache_type','num_ctx','context_txt_enabled','model'}:
            raise ValueError('GPU별 설정을 확인하세요.')
        if 'parallel' in values and (type(values['parallel']) is not int or not 1<=values['parallel']<=32):
            raise ValueError('동시 처리는 1~32 사이의 정수로 입력하세요.')
        if 'kv_cache_type' in values and values['kv_cache_type'] not in ('q8_0','q4_0','f16'):
            raise ValueError('KV 캐시 설정을 확인하세요.')
        if 'num_ctx' in values and (type(values['num_ctx']) is not int or not 512<=values['num_ctx']<=262144):
            raise ValueError('컨텍스트는 512~262144 사이의 정수로 입력하세요.')
        if 'context_txt_enabled' in values and type(values['context_txt_enabled']) is not bool:
            raise ValueError('TXT 문맥 설정을 확인하세요.')
        if 'model' in values and values['model'] is not None and (not isinstance(values['model'],str) or values['model'] != values['model'].strip()):
            raise ValueError('GPU별 모델 설정을 확인하세요.')
        clean[ident]=dict(values)
    return dict(inference_preset=preset,inference_overrides=overrides,gpu_overrides=clean)


def profile_values(profile):
    return {key:profile[key] for key in FIELDS}


def inference_preview(devices, preferences, environment=None, data=None, _per_device=True):
    data=data or rules();selected=validate_inference(preferences)
    candidates=[];dual=False
    if _per_device:
        from gpu_execution_plan import candidate_devices,dual_mode
        candidates=candidate_devices(devices,preferences,os.environ if environment is None else environment,strict=False)
        dual=dual_mode(candidates,preferences)
    if dual:
        selected=dict(selected,inference_preset='auto',inference_overrides={})
    _,audit=capacity_profile(devices,preferences,os.environ if environment is None else environment,data)
    profiles={p['id']:p for p in data['profiles']}
    detected=profiles.get(audit.get('profile'))
    # Unknown hardware keeps the existing defaults; this is not a capacity guess.
    recommended=profile_values(detected or profiles['12gb'])
    chosen=detected if selected['inference_preset']=='auto' else profiles[selected['inference_preset']]
    effective=dict(profile_values(chosen) if chosen else recommended,**selected['inference_overrides'])
    reduced_only=bool(audit.get('total_mib',0)>12288)
    warnings=[]
    if reduced_only:
        if effective['parallel']<recommended['parallel']:
            warnings.append('추정 프리셋보다 동시 처리를 낮추면 전체 번역 속도가 느려질 수 있습니다.')
        if effective['num_ctx']<recommended['num_ctx']:
            warnings.append('컨텍스트를 낮추면 한 번에 전달할 수 있는 문맥이 줄어듭니다.')
        if recommended['context_txt_enabled'] and not effective['context_txt_enabled']:
            warnings.append('TXT 문맥을 끄면 준비한 관계·말투 지침이 전달되지 않습니다.')
        if effective['kv_cache_type']=='q4_0':
            warnings.append('KV 캐시 정밀도를 낮추면 번역 결과가 달라질 수 있습니다.')
    elif effective!=recommended:
        if audit.get('vendor')=='amd':
            warnings.append('추정 프리셋과 다릅니다. 메모리 부족이나 출력 오류로 번역이 완료되지 않을 수 있습니다.')
        else:
            warnings.append('시험한 설정과 다릅니다. 메모리 부족이나 출력 오류로 번역이 완료되지 않을 수 있습니다.')
    amd=audit.get('vendor')=='amd'
    result=dict(presets=data['profiles'],detected_profile=audit.get('profile'),total_mib=audit.get('total_mib'),
                vendor=audit.get('vendor'),backend=audit.get('backend'),
                support=('estimated' if amd and detected else detected['support'] if detected else 'unknown'),recommended=recommended,effective=effective,
                selected_preset=None if dual else selected['inference_preset'],overrides=selected['inference_overrides'],dual_mode=dual,
                reduced_only=reduced_only,warnings=warnings,
                estimated=bool(amd or (chosen and chosen['support']=='estimated') or reduced_only))
    if _per_device:
        result['devices']=[]
        for device,cuda in candidates:
            item_prefs=dict(preferences,gpu_mode='all')
            if dual:item_prefs.update(inference_preset='auto',inference_overrides={})
            item=inference_preview([device],item_prefs,{},data,_per_device=False)
            values=dict(item['effective'])
            overrides=selected['gpu_overrides'].get(device['id'],{}) if dual else {}
            values.update({k:v for k,v in overrides.items() if k in FIELDS})
            # Reuse the same warning policy against this card's own recommendation.
            warning_prefs=dict(preferences,gpu_mode='all',inference_preset='auto',inference_overrides=values)
            warning_view=inference_preview([device],warning_prefs,{},data,_per_device=False)
            item.update(id=device['id'],name=device.get('name',device['id']),total_mib=device['total'],
                        vendor='nvidia' if cuda else 'amd',backend='cuda' if cuda else device['backend'],
                        effective=values,model=overrides.get('model') or None,
                        missing=([key for key in ('model','parallel') if not overrides.get(key)] if dual else []),
                        selected_preset=None if dual else item['selected_preset'],warnings=warning_view['warnings'])
            result['devices'].append(item)
    return result


def finish_configuration(result, cfg, preview, asset=None):
    overrides=preview['overrides']
    if preview['selected_preset']!='auto':
        result.update(preview['effective'])
    else:result.update(overrides)
    # Resolve absent values once, rather than letting the sender, server and
    # audit each supply a different default for the same job.
    for key,value in preview['effective'].items():result.setdefault(key,value)
    # Name/repair helper sessions deliberately request one slot.
    if cfg.get('_serial_model_request'):
        if result.get('_runtime_device',{}).get('config'): result.update(result['_runtime_device']['config'])
        result['parallel']=1
    audit=result['_hardware_policy']
    audit.update(selected_preset=preview['selected_preset'],overrides=dict(overrides),
                 settings_warnings=preview['warnings'],effective_settings={k:result[k] for k in FIELDS})
    if overrides or preview['selected_preset']!='auto':
        audit['user_customized']=True
        if asset is not None:
            # Baseline measurements no longer describe changed execution settings.
            audit['model_memory_bytes']=estimated_memory(asset,result.get('num_ctx',16384),result.get('parallel',2),rules(),result.get('kv_cache_type','q8_0'))
            audit['memory_basis']='metadata_estimate_not_benchmarked'
        else:
            audit.pop('model_memory_bytes',None);audit.pop('memory_basis',None)
    if preview['estimated']:
        print('GPU preset estimate: 이 GPU의 동시 처리 설정은 추정값입니다. 짧은 구간의 실제 번역 속도를 비교해 조절하세요.',flush=True)
    for warning in preview['warnings']:print('GPU settings warning: '+warning,flush=True)
    print('GPU policy: '+json.dumps(audit,ensure_ascii=False),flush=True)
    return result


def eligible(devices, preferences, environment):
    mode=preferences.get('gpu_mode','auto')
    # CUDA masks select NVIDIA adapters before choosing a capacity baseline.
    nvidia=[d for d in devices if (d.get('vendor')=='nvidia' and d.get('selectable')) or str(d.get('id','')).startswith('GPU-')]
    amd=[d for d in devices if d.get('vendor')=='amd' and str(d.get('id','')).startswith('DXGI-')
         and isinstance(d.get('total'),(int,float)) and d['total']>=5888
         and d.get('selectable',True)]
    if mode=='selected':
        ids=set(preferences.get('gpu_ids',[]))
        nvidia=[d for d in nvidia if d.get('id') in ids]
        amd=[d for d in amd if d.get('id') in ids]
    elif mode=='auto':
        if 'CUDA_VISIBLE_DEVICES' in environment:
            selected=[]
            for value in environment['CUDA_VISIBLE_DEVICES'].split(','):
                value=value.strip()
                if value.isdigit() and int(value)<len(nvidia):selected.append(nvidia[int(value)])
                elif value.startswith('GPU-'):selected.extend(d for d in nvidia if d.get('id','').startswith(value))
            nvidia=selected
        masks=('HIP_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES','GGML_VK_VISIBLE_DEVICES','GPU_DEVICE_ORDINAL','OLLAMA_LLM_LIBRARY')
        vulkan=environment.get('OLLAMA_VULKAN')
        if any(k in environment for k in masks) or (vulkan is not None and vulkan.strip().lower() not in ('1','true')):
            amd=[]
    # Prefer a usable NVIDIA baseline; a too-small adapter must not block a
    # usable AMD device. If no fallback exists retain NVIDIA's error reporting.
    usable_nvidia=[d for d in nvidia if isinstance(d.get('total'),(int,float)) and d['total']>=5888]
    if usable_nvidia: devices=usable_nvidia
    elif amd: devices=amd
    else: devices=nvidia
    return [d for d in devices if isinstance(d.get('total'),(int,float)) and d['total']>0]


def capacity_profile(devices, preferences, environment, data):
    candidates=eligible(devices,preferences,environment)
    if not candidates:return None,dict(status='unknown',reason='No eligible GPU with reported total dedicated capacity')
    # Auto's single-card recommendation uses capacity, not transient free memory.
    # Explicit multi-card selections do not assume their memory can be pooled.
    device=(max if preferences.get('gpu_mode','auto')=='auto' else min)(candidates,key=lambda d:d['total'])
    audit=dict(status='preserved',basis='total_dedicated_vram',total_mib=device['total'],
               devices=[dict(id=d.get('id'),name=d.get('name'),total_mib=d['total']) for d in candidates],
               capacity_device=device.get('id'),used_or_free_vram_consulted=False)
    if device.get('mixed_amd'): audit['mixed_amd']=True
    if device.get('vendor')=='amd':
        backend=device.get('backend')
        verified=(device.get('selectable') and backend in ('vulkan','rocm')
                  and type(device.get(backend+'_index')) is int)
        audit.update(vendor='amd',backend=backend if verified else 'unverified',support='estimated_not_measured')
    options=[p for p in data['profiles'] if device['total']>=p['minimum_total_mib']]
    if not options:
        audit.update(status='unsupported',reason='Below the minimum supported GPU capacity')
        return None,audit
    profile=max(options,key=lambda p:p['minimum_total_mib'])
    if device.get('vendor')=='amd':
        audit.update(status='estimated',profile=profile['id'],support='estimated_not_measured')
        return profile,audit
    if profile['id']=='12gb':
        audit.update(profile=profile['id'],support=profile['support'])
        return None,audit
    if profile['support']=='estimated':
        audit.update(status='estimated',profile=profile['id'],support='estimated_not_measured')
        return profile,audit
    audit.update(status='adapted',profile=profile['id'],support='provisional_capacity_simulation',
                 model_budget_bytes=int(device['total']*1024**2*data['model_budget_fraction']))
    return profile,audit


def model_asset(name, runtime_id=None):
    from model_store import manifest_store,blob,gguf
    weights=manifest_store(runtime_id).model(name)['weight']
    path=blob(weights['digest'])
    if not path.is_file():raise ValueError('선택한 모델 파일이 없습니다: '+str(path))
    return dict(name=name,digest=weights['digest'].removeprefix('sha256:'),path=path,
                bytes=path.stat().st_size,metadata=gguf(path))


def matching_local(digest, calibration, runtime_id=None):
    from model_store import manifest_store,blob,gguf_files,hash_file
    store=manifest_store(runtime_id)
    for name in store.names():
        try:registered=store.model(name)
        except (OSError,ValueError):continue
        if registered['ready'] and registered['weight']['digest']=='sha256:'+digest:
            model_path=blob('sha256:'+digest)
            if model_path.is_file() and model_path.stat().st_size==calibration['bytes']:
                return dict(model=name,path=model_path,digest=digest,registered=True)
    model_path=blob('sha256:'+digest)
    if model_path.is_file() and model_path.stat().st_size==calibration['bytes']:
        if hash_file(model_path)==digest:return dict(path=model_path,digest=digest,registered=False)
    # Only the portable models tree; filename alone is never model identity.
    for path in gguf_files():
        if path.stat().st_size==calibration['bytes'] and hash_file(path)==digest:
            return dict(path=path,digest=digest,registered=False)
    return None


def estimated_memory(asset, ctx, parallel, data, kv_cache_type='q8_0'):
    meta=asset['metadata'];arch=meta.get('general.architecture','')
    try:
        layers=int(meta[arch+'.block_count']);heads=int(meta[arch+'.attention.head_count'])
        kv_heads=int(meta[arch+'.attention.head_count_kv']);embedding=int(meta[arch+'.embedding_length'])
        key=int(meta.get(arch+'.attention.key_length',embedding//heads))
        value=int(meta.get(arch+'.attention.value_length',embedding//heads))
        if min(layers,heads,kv_heads,embedding,key,value)<=0:raise ValueError('Invalid model dimensions')
    except (KeyError,TypeError,ValueError,ZeroDivisionError):return None
    # q8_0 blocks: 32 data bytes + 2 scale bytes. Graph allowance is an estimate.
    bytes_per_element={'q8_0':34/32,'q4_0':18/32,'f16':2}[kv_cache_type]
    kv=layers*kv_heads*(key+value)*ctx*parallel*bytes_per_element
    return math.ceil(asset['bytes']*1.05+kv+data['estimated_graph_bytes'])


def configure(cfg, devices=None, preferences=None, environment=None):
    """Freeze once per job. Offline calls do not register or load any model."""
    if cfg.get('_hardware_policy'):return dict(cfg)
    from desktop_bridge import policy_devices,preferences as saved_preferences
    data=rules();result=dict(cfg)
    preferences=preferences if preferences is not None else saved_preferences()
    environment=environment if environment is not None else os.environ
    try:devices=devices if devices is not None else policy_devices(cfg.get('runtime_id',preferences.get('runtime_id')))
    except Exception as exc:
        result['_hardware_policy']=dict(status='unknown',reason=type(exc).__name__+': '+str(exc))
        preview=inference_preview([],preferences,environment,data)
        if 'inference_preset' in preferences or 'inference_overrides' in preferences:
            result.update(preview['effective'])
        return finish_configuration(result,cfg,preview)
    plan=[]
    from gpu_execution_plan import device_plan,dual_mode
    if not cfg.get('_runtime_device'):
        plan=device_plan(cfg,devices,preferences,environment=environment)
        if plan:
            if cfg.get('_serial_model_request'):
                result['_runtime_device']=dict(plan[0])
                result.update(plan[0]['config'])
                result['parallel']=1
            else:
                result['_gpu_device_plan']=plan
    profile,audit=capacity_profile(devices,preferences,environment,data)
    if dual_mode(plan,preferences):
        # Each device owns its model and inference settings; the first is the
        # baseline for job-wide bookkeeping and serial helper sessions.
        first=plan[0]
        result.update(first['config'],parallel=1 if cfg.get('_serial_model_request') else first['parallel'])
        audit.update(source='per_gpu',selected_preset=None,overrides={},
                     effective_model=result.get('model'),
                     effective_settings={k:result[k] for k in FIELDS})
        result['_hardware_policy']=audit
        return result
    result['_hardware_policy']=audit
    preview=inference_preview(devices,preferences,environment,data,_per_device=not bool(cfg.get('_runtime_device')))
    # Saved application settings are the settings displayed by the model tab.
    # Apply that complete baseline before capacity/model fitting, including when
    # resuming projects written with the old generic-model 8K defaults. Direct
    # programmatic callers without inference preferences keep explicit cfg
    # values; they still receive the same resolved defaults for missing fields.
    if 'inference_preset' in preferences or 'inference_overrides' in preferences:
        result.update(preview['effective'])
    if preview['selected_preset']!='auto':
        profile=next(p for p in data['profiles'] if p['id']==preview['selected_preset'])
        audit.update(applied_profile=profile['id'])
        if profile['id']=='12gb':profile=None
    if audit['status']=='unsupported' and audit.get('vendor')!='amd':
        result['_hardware_error']='자동 설정의 최소 지원 대상은 전용 VRAM 6GB급 GPU입니다.'
    if (preferences.get('gpu_mode','auto')=='auto' and audit.get('mixed_amd') and
            audit.get('capacity_device')):
        result['_hardware_cuda_device']=audit['capacity_device']
    if audit.get('vendor')=='amd' and profile is not None:
        # Capacity tiers are estimates on AMD; do not reuse NVIDIA measurements,
        # alter CUDA masks, or silently change the selected model.
        result.update(profile_values(profile))
        if cfg.get('_serial_model_request'): result['parallel']=1
        return finish_configuration(result,cfg,preview)
    if profile is None:
        return finish_configuration(result,cfg,preview)
    if profile['support']=='estimated':
        result['parallel']=profile['parallel'] if result.get('parallel',2)>1 else 1
        if preferences.get('gpu_mode','auto')=='auto' and audit.get('capacity_device'):
            result['_hardware_cuda_device']=audit['capacity_device']
        return finish_configuration(result,cfg,preview)
    audit.update(requested_model=cfg.get('model'),context_txt_enabled=False,kv_cache_type=profile['kv_cache_type'])
    result.update(context_txt_enabled=False,kv_cache_type=profile['kv_cache_type'],
                  num_ctx=min(result.get('num_ctx',profile['num_ctx']),profile['num_ctx']),
                  parallel=min(result.get('parallel',profile['parallel']),profile['parallel']))
    if preferences.get('gpu_mode','auto')=='auto' and audit.get('capacity_device'):
        result['_hardware_cuda_device']=audit['capacity_device']
    budget=audit.get('model_budget_bytes',int(audit.get('total_mib',profile['minimum_total_mib'])*1024**2*data['model_budget_fraction']))
    asset=None
    try:
        runtime_id=cfg.get('runtime_id',preferences.get('runtime_id') or '')
        asset=model_asset(cfg.get('model'),runtime_id=runtime_id);calibrations=data['calibrated_models']
        current=calibrations.get(asset['digest'])
        if current:
            measured=current.get('gpu_bytes',{}).get(profile['id'])
            requirement=measured if measured is not None else estimated_memory(asset,result['num_ctx'],result['parallel'],data)
            fits=requirement is not None and requirement<=budget
            if not fits:
                alternatives=[(digest,item) for digest,item in calibrations.items()
                              if item['family']==current['family'] and item.get('gpu_bytes',{}).get(profile['id'],math.inf)<=budget]
                candidate=None
                for digest,item in sorted(alternatives,key=lambda x:x[1]['bytes'],reverse=True):
                    candidate=matching_local(digest,item,runtime_id=runtime_id)
                    if candidate:
                        asset=dict(asset,bytes=item['bytes'],digest=digest)
                        audit.update(model_quantization=item['quantization'],model_memory_bytes=item['gpu_bytes'][profile['id']],
                                     memory_basis='measured_model_buffers',effective_weight_digest=digest)
                        if candidate['registered']:result['model']=candidate['model']
                        else:result['_hardware_pending_model']=dict(path=str(candidate['path']),digest=digest,info=item)
                        break
                if not candidate:
                    files=', '.join(item['filename'] for _,item in alternatives)
                    raise ValueError('이 GPU에 맞는 같은 모델의 작은 양자화 파일이 필요합니다. AI 모델 탭에서 준비하세요: '+(files or '더 작은 모델'))
            else:audit.update(model_memory_bytes=requirement,memory_basis='measured_model_buffers' if measured is not None else 'metadata_estimate_not_benchmarked')
        else:
            # Unknown models are not silently replaced with HY. Fit their own metadata.
            candidates=[(result['num_ctx'],result['parallel']),(result['num_ctx'],1)]
            if result['num_ctx']>4096:candidates.append((4096,1))
            fit=None
            for ctx,parallel in candidates:
                size=estimated_memory(asset,ctx,parallel,data)
                if size is not None and size<=budget:fit=(ctx,parallel,size);break
            if fit is None:raise ValueError('선택한 모델을 이 GPU에 맞게 배치할 수 없습니다. AI 모델 탭에서 더 작은 모델을 선택하세요. 모델 구조 정보가 부족한 경우에도 자동 적합 판정을 하지 않습니다.')
            result.update(num_ctx=fit[0],parallel=fit[1]);audit.update(model_memory_bytes=fit[2],memory_basis='metadata_estimate_not_benchmarked')
    except (OSError,ValueError,TypeError) as exc:
        result['_hardware_error']=str(exc);audit['model_fit_error']=str(exc)
    audit.update(effective_model=result.get('model'),num_ctx=result.get('num_ctx'),parallel=result.get('parallel'))
    if result.get('_hardware_pending_model'):audit['pending_model_file']=result['_hardware_pending_model']['path']
    return finish_configuration(result,cfg,preview,asset)


def prepare_model(cfg):
    result=dict(cfg)
    if result.get('_hardware_error'):raise ValueError(result['_hardware_error'])
    pending=result.pop('_hardware_pending_model',None)
    if pending:
        from model_store import register
        info=pending['info']
        runtime_options={'runtime_id':result['runtime_id']} if 'runtime_id' in result else {}
        result['model']=register(Path(pending['path']),dict(filename=info['filename'],repo=info.get('repo','')),digest=pending['digest'],manual=True,
                                 **runtime_options)
        result['_hardware_policy']=dict(result['_hardware_policy'],effective_model=result['model'])
    return result


def translation_context(context, cfg):
    """Final guard for direct callers as well as the SourceContext pipeline."""
    if cfg.get('context_txt_enabled',True) is not False:return context
    result={k:v for k,v in context.items() if k not in ('translation_guidance','current_state')}
    audit=dict(result.get('_context_audit',{}))
    audit.update(files={},scope='',context_txt_enabled=False,
                 skip_reason='GPU hardware policy',gpu_profile=cfg.get('_hardware_policy',{}).get('profile'))
    result['_context_audit']=audit
    return result
