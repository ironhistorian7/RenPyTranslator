import type {Job,TranslationReview} from './types';
const el=<T extends HTMLElement>(id:string)=>document.getElementById(id) as T;
type Sentence=Extract<TranslationReview,{kind:'sentence'}>;
type Choice={mode:'keep'|'variant'|'text';variant:string;text:string};
export class TranslationReviewPanel {
  private dialog=el<HTMLDialogElement>('translation-review'); private fingerprint:string|null=null; private busy=false;
  private sentence:Sentence|null=null; private choices=new Map<string,Choice>(); private page=0;
  constructor(private update:(job:Job)=>void){
    el<HTMLButtonElement>('translation-review-submit').addEventListener('click',()=>void this.submit());
    el<HTMLButtonElement>('translation-review-cancel').addEventListener('click',()=>void this.cancel());
    this.dialog.addEventListener('cancel',event=>{event.preventDefault();void this.cancel();});
  }
  render(job:Job){
    const review=job.running&&!job.canceling?job.translation_review:null;
    if(!review){if(this.dialog.open)this.dialog.close();if(!job.running||job.canceling){this.fingerprint=null;this.sentence=null;this.choices.clear();}return;}
    const same=this.fingerprint===review.fingerprint&&!!this.sentence===(review.kind==='sentence');
    if(same&&this.dialog.open){this.error(review.error||'');return;}
    if(!same){this.fingerprint=review.fingerprint;this.busy=false;this.choices.clear();this.page=0;}
    this.sentence=review.kind==='sentence'?review:null;
    el('translation-review-title').textContent=this.sentence?'문장 번역 확인':'미번역 항목 확인';
    el('translation-review-help').textContent=this.sentence?'같은 원문에 여러 번역이 있습니다. 바꿀 항목만 선택하세요.':'직접 입력을 선택한 항목만 저장합니다. 선택하지 않은 항목은 현재 내용을 유지합니다.';
    if(review.kind==='sentence')this.renderSentence();else this.renderManual(review);
    this.error(review.error||'');if(!this.dialog.open)this.dialog.showModal();
  }
  private renderManual(review:Exclude<TranslationReview,{kind:'sentence'}>){
    const body=el<HTMLDivElement>('translation-review-body');body.replaceChildren();el('translation-review-count').hidden=true;
    for(const entry of review.entries){
      const row=document.createElement('div');row.className='translation-review-row';row.dataset.id=entry.id;
      const source=document.createElement('div');source.className='translation-review-source';source.textContent='원문: '+entry.source;
      const current=document.createElement('div');current.className='translation-review-source';current.textContent='현재: '+entry.current;
      const label=document.createElement('label');const check=document.createElement('input');check.type='checkbox';label.append(check,document.createTextNode(' 직접 입력 적용'));
      const edit=document.createElement('textarea');edit.value=entry.current;edit.setAttribute('aria-label','직접 입력');edit.addEventListener('input',()=>{check.checked=true;});
      if(entry.issue||entry.location){const location=document.createElement('div');location.className='translation-review-source';location.textContent=entry.issue||entry.location||'';row.append(location);}
      row.append(source,current,label,edit);body.append(row);
    }
  }
  private renderSentence(){
    const review=this.sentence!;
    const body=el<HTMLDivElement>('translation-review-body');
    body.replaceChildren();

    const nav=document.createElement('div');
    nav.className='sentence-review-navigation';
    const previous=document.createElement('button');
    previous.type='button';
    previous.className='secondary';
    previous.textContent='이전';
    previous.disabled=this.page===0;
    previous.onclick=()=>{this.page--;this.renderSentence();};

    const position=document.createElement('span');
    position.textContent=review.groups.length?`${this.page+1} / ${review.groups.length}`:'확인할 문장 없음';
    position.setAttribute('aria-live','polite');

    const next=document.createElement('button');
    next.type='button';
    next.className='secondary';
    next.textContent='다음';
    next.disabled=this.page>=review.groups.length-1;
    next.onclick=()=>{this.page++;this.renderSentence();};
    nav.append(previous,position,next);
    body.append(nav);

    const group=review.groups[this.page];
    if(!group){this.updateCount();return;}
    const choice=this.choices.get(group.id)||{mode:'keep',variant:'',text:''};
    this.choices.set(group.id,choice);

    const meta=document.createElement('div');
    meta.className='sentence-review-meta';
    meta.textContent=[group.scope,group.speaker&&`화자: ${group.speaker}`,group.listener&&`상대: ${group.listener}`].filter(Boolean).join(' · ');
    const source=document.createElement('div');
    source.className='sentence-review-source';
    const caption=document.createElement('span');
    caption.textContent='원문';
    const original=document.createElement('div');
    original.textContent=group.source;
    source.append(caption,original);
    body.append(meta,source);

    const fieldset=document.createElement('fieldset');
    fieldset.className='sentence-review-options';
    const legend=document.createElement('legend');
    legend.textContent='적용할 번역';
    fieldset.append(legend);

    const option=(titleText:string,mode:Choice['mode'],variant?:Sentence['groups'][number]['variants'][number])=>{
      const label=document.createElement('label');
      label.className='sentence-review-option';
      const radio=document.createElement('input');
      radio.type='radio';
      radio.name='sentence-choice';
      radio.checked=choice.mode===mode&&(mode!=='variant'||choice.variant===variant?.id);
      const content=document.createElement('span');
      content.className='sentence-review-option-content';
      const title=document.createElement('span');
      title.textContent=titleText;
      content.append(title);
      if(variant){
        const detail=document.createElement('small');
        const editable=variant.editable_count;
        const mixed=typeof editable==='number'&&editable>0&&editable<variant.count;
        detail.textContent=[`${variant.count}곳`,mixed?`변경 가능 ${editable}곳`:'',variant.manual?'직접 입력 유지':'',variant.location].filter(Boolean).join(' · ');
        content.append(detail);
      }
      radio.onchange=()=>{
        choice.mode=mode;
        if(variant)choice.variant=variant.id;
        this.updateCount();
      };
      label.append(radio,content);
      fieldset.append(label);
      return label;
    };

    option('각 위치의 현재 번역 유지','keep');
    for(const variant of group.variants)option(variant.text,'variant',variant);
    const custom=option('직접 입력','text');
    const edit=document.createElement('textarea');
    edit.value=choice.text;
    edit.placeholder='적용할 번역을 입력하세요';
    edit.setAttribute('aria-label','직접 입력할 번역');
    edit.oninput=()=>{
      choice.text=edit.value;
      choice.mode='text';
      custom.querySelector('input')!.checked=true;
      this.updateCount();
    };
    custom.append(edit);
    body.append(fieldset);
    this.updateCount();
  }
  private updateCount(){
    const n=el('translation-review-count');
    const count=Array.from(this.choices.values()).filter(choice=>choice.mode!=='keep').length;
    n.textContent=`변경 선택 ${count}개`;
    n.hidden=false;
  }
  private error(value:string){
    const n=el('translation-review-error');
    n.textContent=value;
    n.hidden=!value;
  }
  private async submit(){
    if(this.busy||!this.fingerprint)return;
    this.busy=true;
    const reply=this.sentence?{
      kind:'sentence' as const,
      fingerprint:this.fingerprint,
      selections:Array.from(this.choices,([id,choice])=>
        choice.mode==='variant'?{id,variant:choice.variant}:
        choice.mode==='text'?{id,text:choice.text}:null
      ).filter((item):item is {id:string;variant:string}|{id:string;text:string}=>item!==null)
    }:{
      fingerprint:this.fingerprint,
      entries:Array.from(this.dialog.querySelectorAll<HTMLDivElement>('.translation-review-row')).map(row=>({
        id:row.dataset.id!,
        apply:row.querySelector('input')!.checked,
        text:row.querySelector('textarea')!.value
      }))
    };
    try{
      const result=await window.rpt.translationReview(reply);
      if(result.ok)this.update(result.value);
      else this.error(result.error);
    }catch(error){
      this.error(String(error));
    }finally{
      this.busy=false;
    }
  }
  private async cancel(){if(this.busy)return;const result=await window.rpt.cancel();if(result.ok)this.update(result.value);else this.error(result.error);}
}
