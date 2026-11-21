import type {InferencePreview,InferenceValues,Settings} from './types';

export function inferenceSummary(preview:InferencePreview,settings?:Settings):{summary:string;warning:string}{
  const devices=preview.devices||[];
  const format=(v:InferenceValues)=>`문맥 ${v.num_ctx.toLocaleString()} 토큰 · 동시 처리 ${v.parallel} · KV ${v.kv_cache_type} · TXT 문맥 ${v.context_txt_enabled?'사용':'제외'}`;
  if(!devices.length)return {summary:preview.devices?'사용 가능한 GPU 없음':format(preview.effective),warning:[...new Set(preview.warnings)].join(' ')};
  const dual=settings&&settings.gpu_mode!=='auto'&&devices.length>=2;
  const summary=devices.length===1?format(devices[0].effective):devices.map(d=>`${d.name}: ${dual&&settings.gpu_overrides?.[d.id]?.parallel===undefined?format(d.effective).replace(`동시 처리 ${d.effective.parallel}`,'동시 처리 입력 필요'):format(d.effective)}`).join(' / ');
  const byWarning=new Map<string,string[]>();
  for(const d of devices)for(const raw of new Set(d.warnings)){
    const warning=devices.reduce((text,device)=>text.replaceAll(device.id,device.name),raw);
    const ids=byWarning.get(warning)||[];ids.push(d.name);byWarning.set(warning,ids);
  }
  const warning=[...byWarning].map(([text,ids])=>devices.length===1?text:`${ids.join(', ')}: ${text}`).join(' ');
  return {summary,warning};
}
