const translations=new Set(['run','retranslate','update']);

export function selectTask(selected:Set<string>,task:string,checked:boolean):Set<string>{
  const next=new Set(selected);
  if(!checked){next.delete(task);return next;}
  if(translations.has(task))for(const previous of translations)next.delete(previous);
  next.add(task);
  return next;
}
