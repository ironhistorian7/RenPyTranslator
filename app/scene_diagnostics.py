"""Local scene-analysis diagnostics. No inference or script extraction here."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from engine import save_json


def location(choice, guards=None, evidence=None):
    result={k:choice.get(k) for k in ('id','file','line','menu_line','label','title')}
    result['guards']=list(guards if guards is not None else choice.get('guards',[]))
    if evidence:
        result['scene_file']=evidence.get('file')
        result['scene_line']=evidence.get('line')
    return result


class SceneDiagnostics:
    def __init__(self, project, model):
        self.path=Path(project)/'data/scene-diagnostics.json'
        self.errors=Path(project)/'data/scene-errors.jsonl'
        self.data={'version':1,'run_id':uuid.uuid4().hex,'started_at':self.now(),
                   'model':model,'status':'running','choices':{},'segments':{}}
        self.warned=False

    @staticmethod
    def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')

    def warning(self, exc):
        if not self.warned:
            print('Scene diagnostic log could not be written: '+str(exc),flush=True)
            self.warned=True

    def flush(self):
        self.data['updated_at']=self.now()
        try:save_json(self.path,self.data)
        except OSError as exc:self.warning(exc)

    def choice(self, choice, unique, walked, limited, stops, items):
        total=sum(e['kind']=='dialogue' and bool(e.get('text')) for e in walked)
        remaining=sum(e['kind']=='dialogue' and bool(e.get('text')) for e in unique)
        reason=('has_scene_input' if items else
                'shared_dialogue_removed' if total and not remaining else 'no_followed_dialogue')
        self.data['choices'][choice['id']]=dict(location(choice),reason=reason,
            walked_dialogue_count=total,unique_dialogue_count=remaining,
            traversal_limited=limited,stops=stops,segment_keys=[item['key'] for item in items])
        media=[e for e in unique if e['kind']=='media']
        self.data['choices'][choice['id']]['media']=[{k:e.get(k) for k in
            ('file','line','media_name','assets','animation','video','media_resolved','guards','missing_assets')} for e in media]
        if media and not remaining:
            self.data['choices'][choice['id']]['reason']='media_only' if any(e.get('media_resolved') for e in media) else 'unresolved_media'
        for item in items:self.register(item)

    def register(self,item):
        record=self.data['segments'].setdefault(item['key'],{
            'status':'pending','locations':[],'excerpt':item['payload'].get('excerpt',False),
            'input_dialogue_count':len(item['payload']['dialogue'])})
        for loc in item.get('locations',[]):
            if loc not in record['locations']:record['locations'].append(loc)
        return record

    def result(self,item,status,reason,cached=False):
        record=self.register(item)
        record.update(status=status,reason=reason,cached=cached)

    def failure(self,batch,failures,model,options,response=None,error=None,batch_number=None,instruction=None):
        """One append per failed batch; retain exact response and only failed inputs."""
        selected=[]
        for index,reason in failures:
            key,item=batch[index]
            status='cancelled' if reason=='cancelled' else 'failed'
            record=self.register(item)
            record.update(status=status,reason=reason,cached=False)
            selected.append({'batch_index':index,'key':key,'reason':reason,
                             'locations':record['locations'],'input':item['payload']})
        if not selected:return
        entry={'version':1,'run_id':self.data['run_id'],'time':self.now(),
               'batch_number':batch_number,'model':model,'options':options,
               'instruction':instruction,
               'failures':selected,'response':response,
               'exception':{'type':type(error).__name__,'message':str(error)} if error else None}
        try:
            self.errors.parent.mkdir(parents=True,exist_ok=True)
            with self.errors.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(entry,ensure_ascii=False)+'\n')
                stream.flush()
        except OSError as exc:self.warning(exc)
        self.flush()
        print('Scene analysis error log: '+str(self.errors),flush=True)

    def finish(self,status='completed'):
        self.data['status']=status
        self.flush()
