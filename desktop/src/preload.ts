import {contextBridge,ipcRenderer} from 'electron';
import type {DesktopAPI,Event} from './types';
const api:DesktopAPI = {
  info:()=>ipcRenderer.invoke('rpt:info'), state:()=>ipcRenderer.invoke('rpt:state'),
  browse:()=>ipcRenderer.invoke('rpt:browse'), save:s=>ipcRenderer.invoke('rpt:save',s),
  inferencePreview:s=>ipcRenderer.invoke('rpt:inferencePreview',s),
  project:r=>ipcRenderer.invoke('rpt:project',r),
  start:r=>ipcRenderer.invoke('rpt:start',r), cancel:()=>ipcRenderer.invoke('rpt:cancel'),
  nameReview:r=>ipcRenderer.invoke('rpt:nameReview',r),
  translationReview:r=>ipcRenderer.invoke('rpt:translationReview',r),
  reset:()=>ipcRenderer.invoke('rpt:reset'),
  open:w=>ipcRenderer.invoke('rpt:open',w), appearance:t=>ipcRenderer.invoke('rpt:appearance',t),
  support:r=>ipcRenderer.invoke('rpt:support',r),
  model:r=>ipcRenderer.invoke('rpt:model',r),modelWork:r=>ipcRenderer.invoke('rpt:modelWork',r),modelCancel:()=>ipcRenderer.invoke('rpt:modelCancel'),
  runtime:r=>ipcRenderer.invoke('rpt:runtime',r),runtimeWork:r=>ipcRenderer.invoke('rpt:runtimeWork',r),
  subscribe:callback=>{const listener=(_:unknown,event:Event)=>callback(event);ipcRenderer.on('rpt:event',listener);return()=>ipcRenderer.removeListener('rpt:event',listener);}
};
contextBridge.exposeInMainWorld('rpt',api);
