import type {ModelLibrary,ModelRepo,ModelDetail,ModelFile,ModelJob,Snapshot,Reply} from './types';
const el=(id:string)=>document.getElementById(id)!;
const btn=(id:string)=>el(id) as HTMLButtonElement;
const choose=(id:string)=>el(id) as HTMLSelectElement;
const bytes=(n:number|null|undefined)=>n==null?'미제공':(n/1e9).toFixed(2)+' GB';
export class ModelsPanel{
  library:ModelLibrary|null=null; detail:ModelDetail|null=null; file:ModelFile|null=null;
  repos:ModelRepo[]=[];
  snapshot:Snapshot|null=null;busy=false;locked=false;loading=false;searched=false;generation=0;
  constructor(private onBusy:(busy:boolean)=>void,private notify:(s:string,error?:boolean)=>void){
    btn('model-folder').onclick=()=>{void this.check(window.rpt.open('models'));};
    btn('scan-models').onclick=()=>{void this.work({op:'refresh'});};
    btn('apply-model').onclick=()=>{void this.select();};
    btn('delete-model').onclick=()=>{void this.remove();};
    choose('installed-model').onchange=()=>{this.controls();this.installedInfo();};
    btn('search-models').onclick=()=>{void this.search();};
    el('model-query').onkeydown=e=>{if(e.key==='Enter')void this.search();};
    for(const kind of ['repo','file']){
      btn(kind+'-toggle').onclick=()=>this.toggle(kind);
      el(kind+'-options').onkeydown=e=>{const options=Array.from(el(kind+'-options').querySelectorAll<HTMLButtonElement>('button'));let i=options.indexOf(document.activeElement as HTMLButtonElement);
        if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();i=(i+(e.key==='ArrowDown'?1:-1)+options.length)%options.length;options[i]?.focus();}
        if(e.key==='Escape'){this.toggle(kind,false);btn(kind+'-toggle').focus();}};
    }
    btn('download-model').onclick=()=>{if(this.detail&&this.file&&!this.installed(this.file))void this.work({op:'download',repo:this.detail.id,file:this.file.name,revision:this.detail.revision});};
    btn('model-cancel').onclick=()=>{void this.check(window.rpt.modelCancel());};
  }
  async check<T>(p:Promise<Reply<T>>):Promise<T|undefined>{try{const r=await p;if(!r.ok){this.notify(r.error,true);return;}return r.value;}catch(e){this.notify(String(e),true);}}
  toggle(kind:string,open?:boolean){const list=el(kind+'-options');list.hidden=open===undefined?!list.hidden:!open;btn(kind+'-toggle').setAttribute('aria-expanded',String(!list.hidden));if(!list.hidden)list.querySelector<HTMLButtonElement>('button')?.focus();}
  async enter(){await this.refresh();if(!this.searched)await this.search();}
  async refresh(){const r=await this.check(window.rpt.model<ModelLibrary>({op:'inventory'}));if(r)this.renderLibrary(r);}
  renderLibrary(r:ModelLibrary){this.library=r;const options=[new Option('선택 안 함','')];for(const m of r.installed)options.push(new Option(m.title+(m.title!==m.id?' · '+m.id:'')+' · '+bytes(m.size)+(m.ready?'':' · 파일 누락'),m.id));
    choose('installed-model').replaceChildren(...options);choose('installed-model').value=r.selected||'';
    el('model-selection').textContent=r.selected?'사용 중인 설정: '+r.selected:'선택된 모델이 없습니다.';
    el('local-models-help').textContent=r.pending.length?`${r.pending.length}개 GGUF 파일을 찾았습니다. 목록 새로고침을 누르면 등록합니다.`:'받은 단일 GGUF 파일을 models 폴더 안 원하는 위치에 넣고 목록을 새로고침하세요.';
    this.installedInfo();this.repoBadges();if(this.detail)this.renderFiles(false);this.controls();}
  installed(file:ModelFile|null){return file?.sha?this.library?.installed.find(m=>m.ready&&m.sha===file.sha&&m.size===file.size):undefined;}
  repoBadges(){for(const b of el('repo-options').querySelectorAll<HTMLButtonElement>('button[data-repo]')){
    b.querySelector('.installed-badge')?.remove();const repo=this.repos.find(r=>r.id===b.dataset.repo);if(!repo)continue;
    const files=this.detail?.id===repo.id?this.detail.files:repo.files||[];
    const models=this.library?.installed.filter(m=>m.ready&&(m.source===repo.id||files.some(f=>f.sha&&f.sha===m.sha&&f.size===m.size)))||[];
    if(models.length){const badge=document.createElement('small');badge.className='model-badge installed-badge';const quants=[...new Set(models.map(m=>m.quantization).filter(q=>q&&q!=='미제공'))];badge.textContent='설치됨'+(quants.length?' · '+quants.join(', '):'');b.append(badge);}
  }}
  async select(){const r=await this.check(window.rpt.model<ModelLibrary>({op:'select',model:choose('installed-model').value||null}));if(r){this.renderLibrary(r);this.notify(r.selected?'사용할 번역 모델을 저장했습니다.':'모델 선택을 해제했습니다.');}}
  async remove(){const id=choose('installed-model').value;if(!id)return;const r=await this.check(window.rpt.model<ModelLibrary>({op:'delete',model:id}));if(r)this.renderLibrary(r);}
  budget(){if(!this.snapshot)return null;let devices=this.snapshot.gpus.filter(g=>g.total!==null);
    const s=this.snapshot.settings;if(s.gpu_mode==='selected')devices=devices.filter(g=>s.gpu_ids.includes(g.id));
    if(!devices.length)return null;
    const free=devices.map(g=>Math.max(0,(g.total||0)-(g.used||0))*1024**2);
    return s.gpu_mode==='auto'?Math.max(...free):free.reduce((a,b)=>a+b,0);}
  warning(memory:number|null){const budget=this.budget();return !memory?'요구 사양 미확인':budget===null?'GPU 메모리를 확인할 수 없습니다.':memory>budget?'⚠ 예상 메모리가 현재 여유 GPU 메모리보다 큽니다. 느려지거나 실행되지 않을 수 있습니다.':'';}
  installedInfo(){const m=this.library?.installed.find(x=>x.id===choose('installed-model').value);const warning=m?this.warning(m.estimatedMemory):'';el('installed-warning').textContent=warning;el('installed-warning').hidden=!warning;}
  updateHardware(s:Snapshot){this.snapshot=s;this.installedInfo();if(this.detail)this.renderFiles(false);}
  controls(){const locked=this.locked||this.busy;for(const id of ['apply-model','scan-models','installed-model','download-model'])(el(id) as HTMLInputElement).disabled=locked;
    btn('delete-model').disabled=locked||!choose('installed-model').value;btn('download-model').disabled=locked||!this.file||!!this.installed(this.file);
    btn('download-model').textContent=this.installed(this.file)?'설치됨':'다운로드';
    btn('model-cancel').disabled=!this.busy;btn('search-models').disabled=this.loading;}
  async search(){if(this.loading)return;this.loading=true;this.controls();el('catalog-status').textContent='모델 목록을 가져오는 중…';
    const query=(el('model-query') as HTMLInputElement).value;
    const rows=await this.check(window.rpt.model<ModelRepo[]>({op:'search',query}));
    this.loading=false;this.controls();
    if(!rows){el('catalog-status').textContent='목록을 가져오지 못했습니다. 설치된 모델은 계속 사용할 수 있습니다.';return;}
    this.searched=true;this.repos=rows;el('repo-options').replaceChildren();for(const row of rows){const b=document.createElement('button');b.type='button';b.dataset.repo=row.id;b.setAttribute('role','option');
      const title=document.createElement('span');title.textContent=row.id;b.append(title);if(row.recommended){const badge=document.createElement('small');badge.className='model-badge';badge.textContent='추천';b.append(badge);}
      b.onclick=()=>{this.toggle('repo',false);btn('repo-toggle').textContent=row.id+' ⌄';void this.load(row.id);};el('repo-options').append(b);}
    this.repoBadges();el('catalog-status').textContent='공개 GGUF 모델 목록입니다. 번역 품질과 실행 지원은 모델마다 다릅니다.';this.toggle('repo',true);}
  async load(repo:string){const serial=++this.generation;this.detail=null;this.file=null;el('model-detail').hidden=true;this.controls();el('catalog-status').textContent='모델 정보를 가져오는 중…';
    const detail=await this.check(window.rpt.model<ModelDetail>({op:'details',repo}));if(serial!==this.generation)return;
    if(!detail){el('catalog-status').textContent='모델 정보를 가져오지 못했습니다.';return;}
    this.detail=detail;this.file=detail.files[0]||null;el('model-detail').hidden=false;el('model-description').textContent=detail.description;
    el('catalog-status').textContent=detail.files.length?'다운로드할 파일을 선택하세요.':'지원되는 단일 GGUF 파일이 없습니다. 다른 모델을 선택하세요.';
    this.repoBadges();this.renderFiles(true);this.controls();}
  renderFiles(reset:boolean){if(!this.detail)return;const current=this.file?.name;el('file-options').replaceChildren();
    for(const file of this.detail.files){const b=document.createElement('button');b.type='button';b.setAttribute('role','option');b.setAttribute('aria-selected',String(file.name===current));
      const title=document.createElement('span');title.textContent=file.name+' · '+bytes(file.size);b.append(title);
      if(file.recommended){const badge=document.createElement('small');badge.className='model-badge';badge.textContent='추천';b.append(badge);}
      if(this.installed(file)){const badge=document.createElement('small');badge.className='model-badge installed-badge';badge.textContent='설치됨';b.append(badge);}
      const warning=this.warning(file.estimatedMemory);if(warning){const small=document.createElement('small');small.textContent=warning;small.className='warning';b.append(small);}
      b.onclick=()=>{this.file=file;this.toggle('file',false);this.renderFacts();this.controls();};el('file-options').append(b);}
    this.renderFacts();if(reset)this.toggle('file',false);}
  renderFacts(){if(!this.detail)return;const d=this.detail,f=this.file;btn('file-toggle').textContent=f?f.name+' ⌄':'파일 없음';
    if(f){const installed=this.installed(f);el('catalog-status').textContent=installed?'이미 설치되어 있습니다. 위의 사용할 번역 모델에서 선택하고 적용하세요.':'다운로드할 파일을 선택하세요.';if(installed)btn('file-toggle').textContent=f.name+' · 설치됨 ⌄';}
    const data=[['제작자',d.author],['다운로드',bytes(f?.size)],['양자화',f?.quantization||'미제공'],['파라미터',d.parameters?(d.parameters/1e9).toFixed(1)+'B':'미제공'],['최대 문맥',d.context?d.context.toLocaleString()+' 토큰':'미제공'],['예상 실행 메모리',bytes(f?.estimatedMemory)+' · 추정'],['지원 언어',String(d.languages||'')||'미제공'],['라이선스',String(d.license||'미제공')],['구조',d.architecture||'미제공']];
    el('model-facts').replaceChildren();for(const [key,value] of data){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=value;el('model-facts').append(dt,dd);}
    const warning=f?this.warning(f.estimatedMemory):'';el('model-warning').hidden=!warning;el('model-warning').textContent=warning;}
  async work(request:Record<string,unknown>){const result=await this.check(window.rpt.modelWork(request));if(result)this.job(result);}
  job(job:ModelJob){const was=this.busy;this.busy=job.running;this.onBusy(job.running);this.controls();el('model-transfer').hidden=!job.stage;el('model-transfer-stage').textContent=job.stage;
    (el('model-progress') as HTMLProgressElement).value=job.total?100*job.done/job.total:0;
    el('model-transfer-size').textContent=job.total?bytes(job.done)+' / '+bytes(job.total):'';el('model-transfer-error').hidden=!job.error;el('model-transfer-error').textContent=job.error||'';
    if(was&&!job.running)void this.refresh();}
}
