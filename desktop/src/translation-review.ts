import type {Job} from './types';
const el=<T extends HTMLElement>(id:string)=>document.getElementById(id) as T;
export class TranslationReviewPanel {
  private dialog=el<HTMLDialogElement>('translation-review');
  private fingerprint:string|null=null;
  private busy=false;
  constructor(private update:(job:Job)=>void){
    el<HTMLButtonElement>('translation-review-submit').addEventListener('click',()=>void this.submit());
    el<HTMLButtonElement>('translation-review-cancel').addEventListener('click',()=>void this.cancel());
    this.dialog.addEventListener('cancel',event=>{event.preventDefault();void this.cancel();});
  }
  render(job:Job){
    const review=job.running&&!job.canceling?job.translation_review:null;
    if(!review){if(this.dialog.open)this.dialog.close();this.fingerprint=null;return;}
    if(this.fingerprint===review.fingerprint){if(review.error)this.error(review.error);return;}
    this.fingerprint=review.fingerprint;this.busy=false;
    const body=el<HTMLDivElement>('translation-review-body');body.replaceChildren();
    for(const entry of review.entries){
      const row=document.createElement('div');row.className='translation-review-row';row.dataset.id=entry.id;
      const source=document.createElement('div');source.className='translation-review-source';source.textContent='원문: '+entry.source;
      const current=document.createElement('div');current.className='translation-review-source';current.textContent='현재: '+entry.current;
      const label=document.createElement('label');const check=document.createElement('input');check.type='checkbox';
      label.append(check,document.createTextNode(' 직접 입력 적용'));
      const edit=document.createElement('textarea');edit.value=entry.current;edit.setAttribute('aria-label','직접 입력');
      edit.addEventListener('input',()=>{check.checked=true;});
      if(entry.issue||entry.location){
        const location=document.createElement('div');location.className='translation-review-source';
        location.textContent=entry.issue||entry.location||'';row.append(location);
      }
      row.append(source,current,label,edit);body.append(row);
    }
    this.error(review.error||'');if(!this.dialog.open)this.dialog.showModal();
  }
  private error(value:string){const n=el('translation-review-error');n.textContent=value;n.hidden=!value;}
  private async submit(){
    if(this.busy||!this.fingerprint)return;this.busy=true;
    const entries=Array.from(this.dialog.querySelectorAll<HTMLDivElement>('.translation-review-row')).map(row=>({id:row.dataset.id!,apply:row.querySelector('input')!.checked,text:row.querySelector('textarea')!.value}));
    try{const result=await window.rpt.translationReview({fingerprint:this.fingerprint,entries});if(result.ok)this.update(result.value);else this.error(result.error);}
    catch(error){this.error(String(error));}finally{this.busy=false;}
  }
  private async cancel(){if(this.busy)return;const result=await window.rpt.cancel();if(result.ok)this.update(result.value);else this.error(result.error);}
}
