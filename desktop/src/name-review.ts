import type {Job} from './types';

const element=<T extends HTMLElement>(id:string)=>document.getElementById(id) as T;

export class NamesReview {
  private dialog=element<HTMLDialogElement>('name-review');
  private fingerprint:string|null=null;
  private edits=new Map<string,string>();
  private busy=false;
  constructor(private update:(job:Job)=>void){
    this.dialog.addEventListener('cancel',event=>event.preventDefault());
    element<HTMLButtonElement>('name-review-submit').addEventListener('click',()=>void this.submit());
    element<HTMLButtonElement>('name-review-cancel').addEventListener('click',()=>void this.cancel());
  }
  render(job:Job){
    const review=job.running&&!job.canceling?job.name_review:null;
    if(!review){if(this.dialog.open)this.dialog.close();this.fingerprint=null;this.edits.clear();this.busy=false;return;}
    if(this.fingerprint===review.fingerprint){if(review.error){this.error(review.error);this.setBusy(false);}if(!this.dialog.open)this.dialog.showModal();return;}
    this.fingerprint=review.fingerprint;this.edits.clear();this.busy=false;
    const needs=element<HTMLDivElement>('name-review-needed'),automatic=element<HTMLDivElement>('name-review-automatic');
    needs.replaceChildren();automatic.replaceChildren();
    element<HTMLDetailsElement>('name-review-details').open=false;
    this.error(review.error||'');this.setBusy(false);
    for(const entry of review.entries){
      this.edits.set(entry.key,entry.value);
      const row=document.createElement('div');row.className='name-review-row';
      const source=document.createElement('div');source.className='name-review-text';
      const originalLabel=document.createElement('span');originalLabel.textContent='원문';
      const original=document.createElement('strong');original.textContent=entry.source;
      const proposalLabel=document.createElement('span');proposalLabel.textContent='자동 제안 · 참고';
      const proposal=document.createElement('div');proposal.textContent=entry.proposed;
      source.append(originalLabel,original,proposalLabel,proposal);
      if(entry.reading){const reading=document.createElement('span');reading.textContent=`읽기: ${entry.reading}`;source.append(reading);}
      if(entry.reason){const reason=document.createElement('span');reason.textContent=({reading_unknown:'읽기 확인 필요',untranslated_letters:'외국 문자 포함',no_automatic_result:'자동 처리 결과 없음',automatic_failed:'자동 처리 실패'} as Record<string,string>)[entry.reason]||'표기 확인 필요';source.append(reason);}
      const edit=document.createElement('div');edit.className='name-review-edit';
      const label=document.createElement('label');label.textContent='사용할 이름';
      const input=document.createElement('input');input.type='text';input.value=entry.value;input.setAttribute('aria-label',`${entry.source} 사용할 이름`);
      input.addEventListener('input',()=>this.edits.set(entry.key,input.value));
      const reset=document.createElement('button');reset.type='button';reset.className='secondary';reset.textContent='원어 유지';
      reset.addEventListener('click',()=>{input.value=entry.source;this.edits.set(entry.key,entry.source);});
      label.append(input);edit.append(label,reset);row.append(source,edit);
      (entry.needs_review?needs:automatic).append(row);
    }
    element('name-review-needed-heading').textContent=`확인할 이름 (${review.entries.filter(entry=>entry.needs_review).length})`;
    element('name-review-automatic-summary').textContent=`자동 처리된 이름 (${review.entries.filter(entry=>!entry.needs_review).length})`;
    if(!this.dialog.open)this.dialog.showModal();
    (needs.querySelector('input')||this.dialog.querySelector<HTMLButtonElement>('#name-review-submit'))?.focus();
  }
  private error(text:string){const node=element('name-review-error');node.textContent=text;node.hidden=!text;}
  private setBusy(busy:boolean){this.busy=busy;element<HTMLButtonElement>('name-review-submit').disabled=busy;element<HTMLButtonElement>('name-review-cancel').disabled=busy;this.dialog.querySelectorAll<HTMLInputElement|HTMLButtonElement>('.name-review-row input,.name-review-row button').forEach(node=>node.disabled=busy);}
  private async submit(){
    if(this.busy||!this.fingerprint)return;
    const fingerprint=this.fingerprint,entries=Array.from(this.edits,([key,value])=>({key,value}));
    this.error('');this.setBusy(true);
    try{const reply=await window.rpt.nameReview({fingerprint,entries});if(this.fingerprint!==fingerprint)return;if(!reply.ok){this.error(reply.error);this.setBusy(false);return;}this.update(reply.value);}
    catch(error){if(this.fingerprint===fingerprint){this.error(error instanceof Error?error.message:'확인하지 못했습니다. 다시 시도하세요.');this.setBusy(false);}}
  }
  private async cancel(){
    if(this.busy)return;this.setBusy(true);this.error('');
    try{const reply=await window.rpt.cancel();if(!reply.ok){this.error(reply.error);this.setBusy(false);return;}this.update(reply.value);}
    catch(error){this.error(error instanceof Error?error.message:'취소하지 못했습니다. 다시 시도하세요.');this.setBusy(false);}
  }
}
