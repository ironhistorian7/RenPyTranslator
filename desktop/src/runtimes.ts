import type {ModelJob,Reply,RuntimeBundle,Snapshot} from './types';
const el=(id:string)=>document.getElementById(id)!;
const select=(id:string)=>el(id) as HTMLSelectElement;
const button=(id:string)=>el(id) as HTMLButtonElement;

export class RuntimesPanel{
  locked=false;busy=false;available:RuntimeBundle[]=[];
  constructor(private onSelect:(id:string)=>void,private onBusy:(value:boolean)=>void,private notify:(text:string,error?:boolean)=>void,private refresh:()=>Promise<void>){
    select('runtime-select').onchange=()=>this.onSelect(select('runtime-select').value);
    button('runtime-releases').onclick=()=>void this.releases();
    select('runtime-release').onchange=()=>this.controls();
    button('runtime-download').onclick=()=>void this.install();
    button('runtime-cancel').onclick=()=>void this.result(window.rpt.modelCancel());
  }
  async result<T>(promise:Promise<Reply<T>>){try{const result=await promise;if(!result.ok){this.notify(result.error,true);return;}return result.value;}catch(error){this.notify(String(error),true);}}
  render(snapshot:Snapshot,id:string){
    const bundles=snapshot.runtimes||[];
    const options=bundles.map(item=>new Option(item.name+(item.default?' · 기본':''),item.default?'':item.id));
    if(!bundles.length)options.push(new Option('설치된 실행 환경 없음',''));
    if(id&&!bundles.some(item=>item.id===id))options.push(new Option(id+' · 파일 누락',id));
    select('runtime-select').replaceChildren(...options);select('runtime-select').value=bundles.some(item=>item.id===id&&item.default)?'':id;
    for(const item of this.available)item.installed=bundles.some(bundle=>bundle.id===item.id);
    this.renderReleases();this.controls();
  }
  renderReleases(){
    const previous=select('runtime-release').value;
    select('runtime-release').replaceChildren(new Option('버전 선택',''),...this.available.map(item=>new Option(item.name+(item.installed?' · 설치됨':''),item.id)));
    if(this.available.some(item=>item.id===previous))select('runtime-release').value=previous;
  }
  async releases(){
    if(this.locked||this.busy)return;this.busy=true;this.onBusy(true);this.controls();
    try{const result=await this.result(window.rpt.runtime<RuntimeBundle[]>({op:'releases',refresh:true}));if(result){this.available=result;this.renderReleases();}}
    finally{this.busy=false;this.onBusy(false);this.controls();}
  }
  async install(){
    const id=select('runtime-release').value;if(!id||this.locked||this.busy)return;
    this.busy=true;this.onBusy(true);this.controls();
    const job=await this.result(window.rpt.runtimeWork({op:'install',runtime_id:id}));
    if(job)this.job(job);else{this.busy=false;this.onBusy(false);this.controls();}
  }
  job(job:ModelJob){
    this.busy=job.running;this.onBusy(job.running);
    el('runtime-transfer').hidden=!job.running&&!job.error;
    el('runtime-stage').textContent=job.stage;
    const progress=el('runtime-progress') as HTMLProgressElement;
    if(job.total>0)progress.value=100*job.done/job.total;else progress.removeAttribute('value');
    el('runtime-error').hidden=!job.error;el('runtime-error').textContent=job.error||'';
    button('runtime-cancel').hidden=!job.running;
    this.controls();if(!job.running)void this.refresh();
  }
  controls(){
    const disabled=this.locked||this.busy;
    for(const id of ['runtime-select','runtime-releases','runtime-release'])(el(id) as HTMLInputElement).disabled=disabled;
    const item=this.available.find(bundle=>bundle.id===select('runtime-release').value);
    button('runtime-download').disabled=disabled||!item||item.installed||item.available===false;
    button('runtime-download').textContent=item?.installed?'설치됨':'다운로드';
  }
}
