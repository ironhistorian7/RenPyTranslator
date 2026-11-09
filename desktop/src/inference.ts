import type {Settings,Snapshot,InferenceValues,InferencePreview,InferenceProfile} from './types';
const el=(id:string)=>document.getElementById(id)!;
const input=(id:string)=>el(id) as HTMLInputElement;
const select=(id:string)=>el(id) as HTMLSelectElement;
const keys=['num_ctx','parallel','kv_cache_type','context_txt_enabled'] as const;
const labels:Record<string,string>={measured:'측정 설정',capacity_simulation:'용량 제한 모의 시험',estimated:'예측 설정'};

export class InferencePanel{
  private settings:Settings|null=null; private preview:InferencePreview|null=null;
  private reference:InferenceValues|null=null; private profiles:InferenceProfile[]=[]; private reducedOnly=false;
  constructor(private change:(preset:string,overrides:Partial<InferenceValues>)=>void,private save:()=>void){
    select('inference-preset').onchange=()=>{this.change(select('inference-preset').value,{});};
    for(const [id,key] of [['inference-context','num_ctx'],['inference-workers','parallel'],['inference-kv','kv_cache_type'],['inference-txt','context_txt_enabled']] as const){
      el(id).addEventListener('input',()=>{
        if(!this.settings||!this.preview)return;
        const overrides={...this.settings.inference_overrides};
        const value=key==='context_txt_enabled'?input(id).checked:key==='kv_cache_type'?select(id).value:Number(input(id).value);
        const base=this.base();
        if(value===base[key])delete overrides[key];else Object.assign(overrides,{[key]:value});
        this.change(this.settings.inference_preset,overrides);
      });
    }
    el('inference-reset').onclick=()=>this.change('auto',{});
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
    this.settings=settings;this.preview=snapshot.inference;this.profiles=this.preview.presets;
    let detected=this.profiles.find(p=>p.id===this.preview!.detected_profile);
    let total=this.preview.total_mib;
    if(settings.gpu_mode!==snapshot.settings.gpu_mode||JSON.stringify(settings.gpu_ids)!==JSON.stringify(snapshot.settings.gpu_ids)){
      let devices=snapshot.gpus.filter(g=>g.total!==null);
      if(settings.gpu_mode==='selected')devices=devices.filter(g=>settings.gpu_ids.includes(g.id));
      total=devices.length?(settings.gpu_mode==='auto'?Math.max(...devices.map(g=>g.total!)):Math.min(...devices.map(g=>g.total!))):null;
      detected=this.profiles.filter(p=>total!==null&&total>=p.minimum_total_mib).at(-1);
    }
    this.reference=detected||this.preview.recommended;this.reducedOnly=total!==null&&total>12288;
    const options=[new Option('자동 · GPU 용량에 맞춤','auto')];
    for(const p of this.profiles)options.push(new Option(`${p.id.replace('gb','GB')}${p.id==='32gb'?' 이상':''} · ${labels[p.support]}`,p.id));
    select('inference-preset').replaceChildren(...options);select('inference-preset').value=settings.inference_preset||'auto';
    const base=this.base(),effective={...base,...settings.inference_overrides};
    if(document.activeElement!==input('inference-context'))input('inference-context').value=String(effective.num_ctx);
    if(document.activeElement!==input('inference-workers'))input('inference-workers').value=String(effective.parallel);
    select('inference-kv').value=effective.kv_cache_type;input('inference-txt').checked=effective.context_txt_enabled;
    el('inference-capacity').textContent=total===null?'GPU 용량 미확인 · 기존 기본값을 사용합니다.':`총 전용 VRAM ${(total/1024).toFixed(1)} GiB · ${detected?.id.replace('gb','GB')||'지원 구간 미만'} 프리셋`;
    const chosen=this.profiles.find(p=>p.id===settings.inference_preset)||detected;
    el('inference-estimate').hidden=!(this.reducedOnly||chosen?.support==='estimated');
    el('inference-support').textContent=chosen?.support==='capacity_simulation'?'6·8GB 설정은 12GB GPU에서 용량을 제한해 시험했습니다. 실제 6·8GB 카드 검증은 아직입니다.':chosen?.support==='measured'?'현재 12GB GPU에서 사용한 기본 설정입니다.':'컨텍스트 창은 자동으로 늘리지 않습니다.';
    const warnings:string[]=[];
    if(this.reducedOnly){
      if(effective.parallel<this.reference.parallel)warnings.push('추정 프리셋보다 동시 처리를 낮추면 전체 번역 속도가 느려질 수 있습니다.');
      if(effective.num_ctx<this.reference.num_ctx)warnings.push('컨텍스트를 낮추면 한 번에 전달할 수 있는 문맥이 줄어듭니다.');
      if(this.reference.context_txt_enabled&&!effective.context_txt_enabled)warnings.push('TXT 문맥을 끄면 준비한 관계·말투 지침이 전달되지 않습니다.');
      if(effective.kv_cache_type==='q4_0')warnings.push('KV 캐시 정밀도를 낮추면 번역 결과가 달라질 수 있습니다.');
    }else if(keys.some(k=>effective[k]!==this.reference![k]))warnings.push('시험한 설정과 다릅니다. 메모리 부족이나 출력 오류로 번역이 완료되지 않을 수 있습니다.');
    el('inference-warning').hidden=!warnings.length;el('inference-warning').textContent=warnings.join(' ');
  }
  controls(locked:boolean,dirty:boolean,ready:boolean){
    for(const id of ['inference-preset','inference-context','inference-workers','inference-kv','inference-txt','inference-reset'])(el(id) as HTMLInputElement).disabled=locked||!ready;
    (el('inference-save') as HTMLButtonElement).disabled=locked||!dirty||!ready;
  }
}
