export type Settings = {
  source_language:'english'|'japanese'; show_reference:boolean;
  output:string; suffix:string; scale:string; language_corner:'left'|'right'; language_margin:number;
  theme:'system'|'light'|'dark'; gpu_mode:'auto'|'all'|'selected'; gpu_ids:string[];
  inference_preset:string; inference_overrides:Partial<InferenceValues>;
  runtime_id?:string;
};
export type InferenceValues={num_ctx:number;parallel:number;kv_cache_type:'q8_0'|'q4_0'|'f16';context_txt_enabled:boolean};
export type InferenceProfile=InferenceValues&{id:string;minimum_total_mib:number;support:'measured'|'capacity_simulation'|'estimated'};
export type InferencePreview={presets:InferenceProfile[];detected_profile:string|null;total_mib:number|null;support:string;recommended:InferenceValues;effective:InferenceValues;selected_preset:string;overrides:Partial<InferenceValues>;reduced_only:boolean;warnings:string[];estimated:boolean};
export type GPU = {id:string;name:string;total:number|null;used:number|null;utilization:number|null;driver:string|null;selectable:boolean;active:boolean|null};
export type Snapshot = {
  root:string;settings:Settings;tasks:Record<string,string>;fixes:string[];defaultModel:string;runtime:string;appVersion?:string;runtimes?:RuntimeBundle[];
  gpus:GPU[];warnings:string[];
  inference:InferencePreview;
  models:{configured_model:string;policy:Partial<Settings>;loaded:{name:string;size:number|null;vram:number|null;quantization:string|null}[]}[];
};
export type Request = {path:string;source:boolean;tasks:string[];settings:Settings;update_source?:string};
export type GameFormat = {source?:boolean;game_name?:string;show_reference?:boolean;textbox_scale?:number|null;engine:string;engine_label:string;supported:boolean;stop_message:string|null;supported_tasks:string[]|null;source_language:Settings['source_language']|null};
export type JobIssue = {code:string;title:string;detail?:string;state:'warning'|'retrying'|'failed';error_id?:string|null;log_path?:string|null};
export type JobRecovery = {state:'waiting'|'resuming';delay_seconds?:number;next_retry_at?:string|null;attempt?:number};
export type Job = {running:boolean;canceling:boolean;exitCode:number|null;result:string|null;stage:string;completed:number|null;total:number|null;started:number|null;
  phase?:string;phase_label?:string;unit?:string|null;eta_seconds?:number|null;elapsed_seconds?:number;updated?:number;finished?:number|null;
  successful?:number;original_kept?:number;cached?:number;issue?:JobIssue|null;recovery?:JobRecovery|null;notices?:string[]};
export type SupportResult = {text?:string;path?:string;cancelled?:boolean;error?:string;code?:string};
export type ModelJob = {running:boolean;stage:string;done:number;total:number;error:string|null;kind?:'model'|'runtime'};
export type RuntimeBundle={id:string;version:string;name:string;installed:boolean;default?:boolean;directory?:string;size?:number;sha256?:string;available?:boolean;published_at?:string};
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
  project(request:{path:string;source?:boolean}):Promise<Reply<GameFormat>>;
  save(settings:Settings):Promise<Reply<Settings>>;
  start(request:Request):Promise<Reply<Job|null>>;
  cancel():Promise<Reply<Job>>;
  reset():Promise<Reply<Job>>;
  open(which:'result'|'logs'|'models'):Promise<Reply<void>>;
  support(request:{op:'copy'|'export'}):Promise<Reply<SupportResult>>;
  model<T>(request:Record<string,unknown>):Promise<Reply<T>>;
  modelWork(request:Record<string,unknown>):Promise<Reply<ModelJob>>;
  modelCancel():Promise<Reply<void>>;
  runtime<T>(request:Record<string,unknown>):Promise<Reply<T>>;
  runtimeWork(request:Record<string,unknown>):Promise<Reply<ModelJob>>;
  appearance(theme:Settings['theme']):Promise<Reply<boolean>>;
  subscribe(callback:(event:Event)=>void):()=>void;
}
declare global {interface Window {rpt:DesktopAPI}}
