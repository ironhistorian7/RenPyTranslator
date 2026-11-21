import type {Settings,Snapshot,InferenceValues,InferencePreview,InferenceProfile,GPUOverride,ModelLibrary} from './types';
const el=(id:string)=>document.getElementById(id)!;
const input=(id:string)=>el(id) as HTMLInputElement;
const select=(id:string)=>el(id) as HTMLSelectElement;
const keys=['num_ctx','parallel','kv_cache_type','context_txt_enabled'] as const;
const labels:Record<string,string>={measured:'측정 설정',capacity_simulation:'용량 제한 모의 시험',estimated:'예측 설정'};

export class InferencePanel{
  private settings:Settings|null=null; private preview:InferencePreview|null=null;
  private reference:InferenceValues|null=null; private profiles:InferenceProfile[]=[]; private reducedOnly=false;
  private nodes=new Map<string,{root:HTMLElement;model:HTMLSelectElement;workers:HTMLInputElement;kv:HTMLSelectElement;context:HTMLInputElement;txt:HTMLSelectElement;reset:HTMLButtonElement;applied:HTMLElement;warning:HTMLElement}>();
  private library:ModelLibrary|null=null; private snapshot:Snapshot|null=null;
  setLibrary(library:ModelLibrary){this.library=library;if(this.settings&&this.snapshot)this.renderDevices(this.settings,this.snapshot);}
  private locked=false; private ready=false;
  constructor(private change:(preset:string,overrides:Partial<InferenceValues>)=>void,private save:()=>void,private changeDevices:(overrides:Record<string,GPUOverride>)=>void=()=>{}){
    select('inference-preset').onchange=()=>{this.change(select('inference-preset').value,{});};
    for(const [id,key] of [['inference-context','num_ctx'],['inference-workers','parallel'],['inference-kv','kv_cache_type'],['inference-txt','context_txt_enabled']] as const){
      el(id).addEventListener('input',()=>{
        if(!this.settings||!this.preview)return;
        const overrides={...this.settings.inference_overrides};
        const value=key==='context_txt_enabled'?input(id).checked:key==='kv_cache_type'?select(id).value:Number(input(id).value);
        const base=this.base();
        // The same value as the aggregate baseline may still override a different GPU's preset.
        if(value===base[key]&&key!=='parallel'&&key!=='kv_cache_type')delete overrides[key];else Object.assign(overrides,{[key]:value});
        this.change(this.settings.inference_preset,overrides);
      });
    }
    el('inference-reset').onclick=()=>this.change('auto',{});
    el('inference-apply-common').onclick=()=>{if(!this.settings)return;const parallel=Number(input('inference-workers').value);if(!Number.isInteger(parallel)||parallel<1||parallel>32)return;this.change(this.settings.inference_preset,{...this.settings.inference_overrides,parallel,kv_cache_type:select('inference-kv').value as InferenceValues['kv_cache_type']});};
    el('inference-save').onclick=()=>this.save();
  }
  private base():InferenceValues{
    const preset=this.profiles.find(p=>p.id===this.settings?.inference_preset);
    return preset||this.reference!;
  }
  effective():InferenceValues|null{
    return this.settings&&this.preview?{...this.base(),...this.settings.inference_overrides}:null;
  }
  render(settings:Settings,snapshot:Snapshot){
    if(!snapshot.inference)return;
    this.settings=settings;this.snapshot=snapshot;this.preview=snapshot.inference;this.profiles=this.preview.presets;
    let detected=this.profiles.find(p=>p.id===this.preview!.detected_profile);
    let total=this.preview.total_mib;
    let amd=this.preview.vendor==='amd';
    let backend=this.preview.backend;
    const runtimeChanged=(settings.runtime_id||'')!==(snapshot.settings.runtime_id||'');
    const selectionChanged=runtimeChanged||settings.gpu_mode!==snapshot.settings.gpu_mode||JSON.stringify(settings.gpu_ids)!==JSON.stringify(snapshot.settings.gpu_ids);
    if(selectionChanged){
      let devices=snapshot.gpus.filter(g=>g.total!==null&&g.selectable);
      if(settings.gpu_mode==='selected')devices=devices.filter(g=>settings.gpu_ids.includes(g.id));
      total=devices.length?(settings.gpu_mode==='auto'?Math.max(...devices.map(g=>g.total!)):Math.min(...devices.map(g=>g.total!))):null;
      detected=this.profiles.filter(p=>total!==null&&total>=p.minimum_total_mib).at(-1);
      amd=devices.length>0 && devices.every(g=>g.vendor==='amd');
      backend=!runtimeChanged&&devices.length===1 && ['vulkan','rocm'].includes(devices[0].backend||'') ? devices[0].backend : null;
    }
    this.reference=detected||this.preview.recommended;this.reducedOnly=total!==null&&total>12288;
    const options=[new Option('자동 · GPU 용량에 맞춤','auto')];
    for(const p of this.profiles)options.push(new Option(`${p.id.replace('gb','GB')}${p.id==='32gb'?' 이상':''} · ${amd?'용량 기준 추정':labels[p.support]}`,p.id));
    select('inference-preset').replaceChildren(...options);select('inference-preset').value=settings.inference_preset||'auto';
    const base=this.base(),effective={...base,...settings.inference_overrides};
    if(document.activeElement!==input('inference-context'))input('inference-context').value=String(effective.num_ctx);
    if(document.activeElement!==input('inference-workers'))input('inference-workers').value=String(effective.parallel);
    select('inference-kv').value=effective.kv_cache_type;input('inference-txt').checked=effective.context_txt_enabled;
    el('inference-capacity').textContent=total===null?'GPU 용량 미확인 · 기존 기본값을 사용합니다.':`${amd?'AMD · 용량 기준 추정 · ':''}총 전용 VRAM ${(total/1024).toFixed(1)} GiB · ${detected?.id.replace('gb','GB')||'지원 구간 미만'} 프리셋`;
    const chosen=this.profiles.find(p=>p.id===settings.inference_preset)||detected;
    el('inference-estimate').hidden=!(amd||this.reducedOnly||chosen?.support==='estimated');
    const planned=backend==='vulkan'?'Vulkan':backend==='rocm'?'ROCm':null;
    const previewIds=new Set((this.preview.devices||[]).map(d=>d.id));
    const active=(snapshot.models||[]).filter(m=>!selectionChanged&&m.loaded.length&&m.device_id&&previewIds.has(m.device_id)&&['vulkan','rocm'].includes(m.device_backend||''));
    const actual=[...new Set(active.map(m=>m.device_backend==='rocm'?'ROCm':'Vulkan'))];
    const backendText=actual.length?`실행 백엔드: ${actual.join(', ')}`:planned?`계획 백엔드: ${planned}`:'백엔드 미확인';
    el('inference-support').textContent=amd?`AMD · ${backendText}`:chosen?.support==='capacity_simulation'?'6·8GB 설정은 12GB GPU에서 용량을 제한해 시험했습니다. 실제 6·8GB 카드 검증은 아직입니다.':chosen?.support==='measured'?'현재 12GB GPU에서 사용한 기본 설정입니다.':'여러 GPU 사용 시 자동 프리셋은 각 GPU 용량에 맞춰 적용합니다.';
    const warnings:string[]=[];
    if(this.reducedOnly){
      if(effective.parallel<this.reference.parallel)warnings.push('추정 프리셋보다 동시 처리를 낮추면 전체 번역 속도가 느려질 수 있습니다.');
      if(effective.num_ctx<this.reference.num_ctx)warnings.push('컨텍스트를 낮추면 한 번에 전달할 수 있는 문맥이 줄어듭니다.');
      if(this.reference.context_txt_enabled&&!effective.context_txt_enabled)warnings.push('TXT 문맥을 끄면 준비한 관계·말투 지침이 전달되지 않습니다.');
      if(effective.kv_cache_type==='q4_0')warnings.push('KV 캐시 정밀도를 낮추면 번역 결과가 달라질 수 있습니다.');
    }else if(keys.some(k=>effective[k]!==this.reference![k]))warnings.push(`${amd?'추정':'시험한'} 설정과 다릅니다. 메모리 부족이나 출력 오류로 번역이 완료되지 않을 수 있습니다.`);
    el('inference-warning').hidden=!warnings.length;el('inference-warning').textContent=warnings.join(' ');
    this.renderDevices(settings,snapshot);
  }
  private updateDevice(id:string,field:keyof GPUOverride,value:number|string|boolean|null){
    if(!this.settings)return;
    const all={...(this.settings.gpu_overrides||{})}, entry={...all[id]};
    if(value===null)delete entry[field];else Object.assign(entry,{[field]:value});
    if(Object.keys(entry).length)all[id]=entry;else delete all[id];
    this.changeDevices(all);
  }
  private renderDevices(settings:Settings,snapshot:Snapshot){
    const container=el('inference-devices');
    const samePolicy=settings.gpu_mode===snapshot.settings.gpu_mode&&JSON.stringify(settings.gpu_ids)===JSON.stringify(snapshot.settings.gpu_ids);
    const reported=this.preview?.devices;
    const candidates=settings.gpu_mode==='auto'?[]:samePolicy&&reported?reported.map(d=>({id:d.id,name:d.name,total:d.total_mib})):snapshot.gpus.filter(g=>g.selectable&&g.total!==null&&g.total>=5888&&(settings.gpu_mode!=='selected'||settings.gpu_ids.includes(g.id)));
    container.hidden=candidates.length<2;
    el('inference-common').hidden=candidates.length>=2;
    const ids=new Set(candidates.map(g=>g.id));
    for(const [id,node] of this.nodes)if(!ids.has(id)){node.root.remove();this.nodes.delete(id);}
    for(const gpu of candidates){
      let node=this.nodes.get(gpu.id);
      if(!node){
        const root=document.createElement('section');root.className='inference-device';
        const title=document.createElement('h4');title.textContent=gpu.name;
        const modelRow=document.createElement('label');modelRow.className='settings-row';modelRow.append(document.createTextNode('모델'));
        const model=document.createElement('select');model.required=true;model.setAttribute('aria-label',`${gpu.name} 모델`);modelRow.append(model);
        const row=document.createElement('label');row.className='settings-row';row.append(document.createTextNode('동시 처리'));
        const workers=document.createElement('input');workers.type='number';workers.min='1';workers.max='32';workers.step='1';workers.required=true;workers.setAttribute('aria-label',`${gpu.name} 동시 처리`);row.append(workers);
        const kvRow=document.createElement('label');kvRow.className='settings-row';kvRow.append(document.createTextNode('KV 캐시'));
        const kv=document.createElement('select');kv.setAttribute('aria-label',`${gpu.name} KV 캐시`);
        for(const value of ['q8_0','q4_0','f16'])kv.append(new Option(value,value));kvRow.append(kv);
        const contextRow=document.createElement('label');contextRow.className='settings-row';contextRow.append(document.createTextNode('컨텍스트 창 (토큰)'));
        const context=document.createElement('input');context.type='number';context.min='512';context.max='262144';context.step='512';context.setAttribute('aria-label',`${gpu.name} 컨텍스트 창`);contextRow.append(context);
        const txtRow=document.createElement('label');txtRow.className='settings-row';txtRow.append(document.createTextNode('TXT 문맥'));
        const txt=document.createElement('select');txt.setAttribute('aria-label',`${gpu.name} TXT 문맥`);for(const [label,value] of [['사용','true'],['제외','false']])txt.append(new Option(label,value));txtRow.append(txt);
        const applied=document.createElement('p');applied.className='help';
        const warning=document.createElement('p');warning.className='warning';warning.setAttribute('role','alert');
        const reset=document.createElement('button');reset.type='button';reset.className='secondary';reset.textContent='이 GPU 초기화';
        workers.addEventListener('input',()=>{const raw=workers.value;if(raw==='')this.updateDevice(gpu.id,'parallel',null);else if(/^\d+$/.test(raw)&&Number(raw)>=1&&Number(raw)<=32)this.updateDevice(gpu.id,'parallel',Number(raw));else workers.value=this.settings?.gpu_overrides?.[gpu.id]?.parallel?.toString()||'';});
        kv.addEventListener('change',()=>this.updateDevice(gpu.id,'kv_cache_type',kv.value||null));
        model.addEventListener('change',()=>this.updateDevice(gpu.id,'model',model.value||null));
        context.addEventListener('input',()=>{const raw=context.value;if(raw==='')this.updateDevice(gpu.id,'num_ctx',null);else if(/^\d+$/.test(raw)&&Number(raw)>=512&&Number(raw)<=262144)this.updateDevice(gpu.id,'num_ctx',Number(raw));});
        context.addEventListener('blur',()=>{if(this.settings&&this.snapshot)this.renderDevices(this.settings,this.snapshot);});
        txt.addEventListener('change',()=>this.updateDevice(gpu.id,'context_txt_enabled',txt.value===''?null:txt.value==='true'));
        reset.addEventListener('click',()=>{if(!this.settings)return;const all={...(this.settings.gpu_overrides||{})};delete all[gpu.id];this.changeDevices(all);});
        root.append(title,modelRow,row,kvRow,contextRow,txtRow,applied,warning,reset);container.append(root);node={root,model,workers,kv,context,txt,reset,applied,warning};this.nodes.set(gpu.id,node);
      }
      const recommendation=this.profiles.filter(p=>gpu.total!==null&&gpu.total>=p.minimum_total_mib).at(-1)||this.preview!.recommended;
      const override=(settings.gpu_overrides||{})[gpu.id]||{};
      const effective={...recommendation,...override};
      const modelOptions=[new Option('모델 선택',''),...(this.library?.installed.filter(m=>m.ready).map(m=>new Option(m.title,m.id))||[])];
      if(override.model&&!modelOptions.some(o=>o.value===override.model))modelOptions.push(new Option(`${override.model} · 파일 누락`,override.model));
      node.model.replaceChildren(...modelOptions);node.model.value=override.model||'';
      if(document.activeElement!==node.context)node.context.value=String(effective.num_ctx);
      node.txt.value=String(effective.context_txt_enabled);
      if(document.activeElement!==node.workers)node.workers.value=override.parallel===undefined?'':String(override.parallel);
      node.workers.placeholder='입력';
      if(document.activeElement!==node.kv)node.kv.value=effective.kv_cache_type;
      node.applied.textContent=!override.model||override.parallel===undefined?'모델과 동시 처리 값을 입력하세요.':'';
      const devicePreview=reported?.find(d=>d.id===gpu.id);
      const previewMatches=devicePreview&&keys.every(k=>effective[k]===devicePreview.effective[k]);
      const warnings=previewMatches?devicePreview.warnings.map(w=>w.replaceAll(gpu.id,gpu.name)):[];
      node.warning.textContent=warnings.join(' ');node.warning.hidden=!warnings.length;
      node.model.disabled=node.workers.disabled=node.kv.disabled=node.context.disabled=node.txt.disabled=this.locked||!this.ready;node.reset.disabled=this.locked||!this.ready||!Object.keys(override).length;
    }
  }
  controls(locked:boolean,dirty:boolean,ready:boolean){
    this.locked=locked;this.ready=ready;
    for(const [id,node] of this.nodes){node.model.disabled=node.workers.disabled=node.kv.disabled=node.context.disabled=node.txt.disabled=locked||!ready;node.reset.disabled=locked||!ready||!Object.keys(this.settings?.gpu_overrides?.[id]||{}).length;}
    for(const id of ['inference-preset','inference-context','inference-workers','inference-kv','inference-txt','inference-reset','inference-apply-common'])(el(id) as HTMLInputElement).disabled=locked||!ready;
    (el('inference-save') as HTMLButtonElement).disabled=locked||!dirty||!ready;
  }
}
