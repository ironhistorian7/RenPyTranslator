import {contextBridge,ipcRenderer} from 'electron';
import type {DesktopAPI,Event} from './types';
const api:DesktopAPI = {
  info:()=>ipcRenderer.invoke('rpt:info'), state:()=>ipcRenderer.invoke('rpt:state'),
  browse:()=>ipcRenderer.invoke('rpt:browse'), save:s=>ipcRenderer.invoke('rpt:save',s),
  start:r=>ipcRenderer.invoke('rpt:start',r), cancel:()=>ipcRenderer.invoke('rpt:cancel'),
  open:w=>ipcRenderer.invoke('rpt:open',w), appearance:t=>ipcRenderer.invoke('rpt:appearance',t),
  model:r=>ipcRenderer.invoke('rpt:model',r),modelWork:r=>ipcRenderer.invoke('rpt:modelWork',r),modelCancel:()=>ipcRenderer.invoke('rpt:modelCancel'),
  subscribe:callback=>{const listener=(_:unknown,event:Event)=>callback(event);ipcRenderer.on('rpt:event',listener);return()=>ipcRenderer.removeListener('rpt:event',listener);}
};
contextBridge.exposeInMainWorld('rpt',api);
