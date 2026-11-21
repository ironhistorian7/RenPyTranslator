import type {ModelLibrary} from './types';

export function modelTitle(id:string|null|undefined,library?:ModelLibrary|null,displayName?:string):string{
  if(!id)return '선택 안 함';
  const title=library?.installed.find(model=>model.id===id)?.title;
  return title&&title!==id?title:displayName||title||id;
}

export function selectedModelId(library:ModelLibrary|null,defaultModel?:string):string|null{
  return library?library.selected:defaultModel||null;
}
