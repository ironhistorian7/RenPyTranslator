export type Settings = {
  source_language:'english'|'japanese';
  output:string; suffix:string; scale:string; language_corner:'left'|'right'; language_margin:number;
  theme:'system'|'light'|'dark'; gpu_mode:'auto'|'all'|'selected'; gpu_ids:string[];
};
export type GPU = {id:string;name:string;total:number|null;used:number|null;utilization:number|null;driver:string|null;selectable:boolean;active:boolean|null};
export type Snapshot = {
  root:string;settings:Settings;tasks:Record<string,string>;fixes:string[];defaultModel:string;runtime:string;
  gpus:GPU[];warnings:string[];
  models:{configured_model:string;policy:Partial<Settings>;loaded:{name:string;size:number|null;vram:number|null;quantization:string|null}[]}[];
};
export type Request = {path:string;source:boolean;tasks:string[];settings:Settings};
export type Job = {running:boolean;canceling:boolean;exitCode:number|null;result:string|null;stage:string;completed:number|null;total:number|null;started:number|null};
export type ModelJob = {running:boolean;stage:string;done:number;total:number;error:string|null};
export type InstalledModel = {id:string;title:string;size:number;sha?:string;ready:boolean;protocol:string;source:string;quantization:string;context:number|null;estimatedMemory:number|null;recommended:boolean;description:string};
export type ModelLibrary = {selected:string|null;installed:InstalledModel[];pending:string[];folder:string};
export type ModelRepo = {id:string;recommended:boolean;downloads?:number;task?:string;files?:ModelFile[]};
export type ModelFile = {name:string;size:number;sha:string;quantization:string;estimatedMemory:number|null;recommended:boolean};
export type ModelDetail = {id:string;revision:string;author:string;license:string|string[];languages:string|string[];parameters:number|null;context:number|null;architecture:string|null;description:string;files:ModelFile[];recommended:boolean;task:string|null;gated:boolean};
export type Event = {type:'log';text:string}|{type:'job';job:Job}|{type:'theme';dark:boolean}|{type:'navigate';page:'models'}|{type:'modelJob';job:ModelJob};
export type Reply<T> = {ok:true;value:T}|{ok:false;error:string};
export interface DesktopAPI {
  info():Promise<Reply<Snapshot>>;
  state():Promise<Reply<{job:Job;log:string;dark:boolean;modelJob:ModelJob}>>;
  browse():Promise<Reply<string|null>>;
  project(request:{path:string;source:boolean}):Promise<Reply<{source_language:Settings['source_language']|null}>>;
  save(settings:Settings):Promise<Reply<Settings>>;
  start(request:Request):Promise<Reply<Job|null>>;
  cancel():Promise<Reply<Job>>;
  open(which:'result'|'logs'|'models'):Promise<Reply<void>>;
  model<T>(request:Record<string,unknown>):Promise<Reply<T>>;
  modelWork(request:Record<string,unknown>):Promise<Reply<ModelJob>>;
  modelCancel():Promise<Reply<void>>;
  appearance(theme:Settings['theme']):Promise<Reply<boolean>>;
  subscribe(callback:(event:Event)=>void):()=>void;
}
declare global {interface Window {rpt:DesktopAPI}}
