"""Job-local GPU policy from installed capacity and portable model metadata."""
import json
import math
import os
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
    return dict(inference_preset=preset,inference_overrides=overrides)


def profile_values(profile):
    return {key:profile[key] for key in FIELDS}


def inference_preview(devices, preferences, environment=None, data=None):
    data=data or rules();selected=validate_inference(preferences)
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
        warnings.append('시험한 설정과 다릅니다. 메모리 부족이나 출력 오류로 번역이 완료되지 않을 수 있습니다.')
    return dict(presets=data['profiles'],detected_profile=audit.get('profile'),total_mib=audit.get('total_mib'),
                support=detected['support'] if detected else 'unknown',recommended=recommended,effective=effective,
                selected_preset=selected['inference_preset'],overrides=selected['inference_overrides'],
                reduced_only=reduced_only,warnings=warnings,
                estimated=bool((chosen and chosen['support']=='estimated') or reduced_only))


def finish_configuration(result, cfg, preview, asset=None):
    overrides=preview['overrides']
    if preview['selected_preset']!='auto':
        result.update(preview['effective'])
    else:result.update(overrides)
    # Name/repair helper sessions deliberately request one slot.
    if cfg.get('_serial_model_request'):result['parallel']=1
    audit=result['_hardware_policy']
    audit.update(selected_preset=preview['selected_preset'],overrides=dict(overrides),
                 settings_warnings=preview['warnings'],effective_settings={k:result.get(k,preview['effective'][k]) for k in FIELDS})
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
    if mode=='selected':
        ids=set(preferences.get('gpu_ids',[]))
        devices=[d for d in devices if d.get('id') in ids]
    elif mode=='auto' and 'CUDA_VISIBLE_DEVICES' in environment:
        mask=environment['CUDA_VISIBLE_DEVICES'].split(',');selected=[]
        for value in mask:
            value=value.strip()
            if value.isdigit() and int(value)<len(devices):selected.append(devices[int(value)])
            elif value.startswith('GPU-'):selected.extend(d for d in devices if d.get('id','').startswith(value))
        devices=selected
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
    options=[p for p in data['profiles'] if device['total']>=p['minimum_total_mib']]
    if not options:
        audit.update(status='unsupported',reason='Below the minimum supported GPU capacity')
        return None,audit
    profile=max(options,key=lambda p:p['minimum_total_mib'])
    if profile['id']=='12gb':
        audit.update(profile=profile['id'],support=profile['support'])
        return None,audit
    if profile['support']=='estimated':
        audit.update(status='estimated',profile=profile['id'],support='estimated_not_measured')
        return profile,audit
    audit.update(status='adapted',profile=profile['id'],support='provisional_capacity_simulation',
                 model_budget_bytes=int(device['total']*1024**2*data['model_budget_fraction']))
    return profile,audit


def model_asset(name):
    from model_store import manifests,read_json,blob,gguf
    manifest=manifests().get(name)
    if manifest is None:raise ValueError('선택한 모델의 로컬 등록 파일을 찾을 수 없습니다: '+str(name))
    weights=next((r for r in read_json(manifest,{}).get('layers',[]) if r.get('mediaType')=='application/vnd.ollama.image.model'),None)
    if not weights:raise ValueError('선택한 모델에 GGUF 가중치 정보가 없습니다: '+str(name))
    path=blob(weights['digest'])
    if not path.is_file():raise ValueError('선택한 모델 파일이 없습니다: '+str(path))
    return dict(name=name,digest=weights['digest'].removeprefix('sha256:'),path=path,
                bytes=path.stat().st_size,metadata=gguf(path))


def matching_local(digest, calibration):
    from model_store import manifests,read_json,blob,gguf_files,hash_file
    for name,path in manifests().items():
        if any(r.get('digest')=='sha256:'+digest and r.get('mediaType')=='application/vnd.ollama.image.model'
               for r in read_json(path,{}).get('layers',[])):
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
    from desktop_bridge import nvidia_devices,preferences as saved_preferences
    data=rules();result=dict(cfg)
    preferences=preferences if preferences is not None else saved_preferences()
    environment=environment if environment is not None else os.environ
    try:devices=devices if devices is not None else nvidia_devices()
    except Exception as exc:
        result['_hardware_policy']=dict(status='unknown',reason=type(exc).__name__+': '+str(exc))
        return finish_configuration(result,cfg,inference_preview([],preferences,environment,data))
    profile,audit=capacity_profile(devices,preferences,environment,data)
    result['_hardware_policy']=audit
    preview=inference_preview(devices,preferences,environment,data)
    if preview['selected_preset']!='auto':
        profile=next(p for p in data['profiles'] if p['id']==preview['selected_preset'])
        audit.update(applied_profile=profile['id'])
        if profile['id']=='12gb':profile=None
    if audit['status']=='unsupported':
        result['_hardware_error']='자동 설정의 최소 지원 대상은 전용 VRAM 6GB급 GPU입니다.'
    if profile is None:
        return finish_configuration(result,cfg,preview)
    if profile['support']=='estimated':
        result['parallel']=profile['parallel'] if cfg.get('parallel',2)>1 else 1
        if preferences.get('gpu_mode','auto')=='auto' and audit.get('capacity_device'):
            result['_hardware_cuda_device']=audit['capacity_device']
        return finish_configuration(result,cfg,preview)
    audit.update(requested_model=cfg.get('model'),context_txt_enabled=False,kv_cache_type=profile['kv_cache_type'])
    result.update(context_txt_enabled=False,kv_cache_type=profile['kv_cache_type'],
                  num_ctx=min(cfg.get('num_ctx',profile['num_ctx']),profile['num_ctx']),
                  parallel=min(cfg.get('parallel',profile['parallel']),profile['parallel']))
    if preferences.get('gpu_mode','auto')=='auto' and audit.get('capacity_device'):
        result['_hardware_cuda_device']=audit['capacity_device']
    budget=audit.get('model_budget_bytes',int(audit.get('total_mib',profile['minimum_total_mib'])*1024**2*data['model_budget_fraction']))
    asset=None
    try:
        asset=model_asset(cfg.get('model'));calibrations=data['calibrated_models']
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
                    candidate=matching_local(digest,item)
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
