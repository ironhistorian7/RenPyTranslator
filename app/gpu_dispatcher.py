"""Per-device runtime leases and bounded, independent failure recovery.

The caller serializes dispatcher methods and remains the sole cache writer.
"""
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from job_events import emit, translation_tracker
from runtime_recovery import Evidence, MAX_RESTARTS, RESTART_DELAYS, cancelled, recoverable


class RuntimeDispatch:
    def __init__(self, pool, project, cfg, saved_count):
        self.pool=pool
        self.project=project
        self.tracker=translation_tracker(cfg)
        self.cfg=dict(cfg)
        self._ui_waiting=False
        self._ui_wait_key=None
        self.total_attempts=0
        self.saved_count=saved_count
        self.states={}
        self.cursor=0
        self.evidence=None
        self.run_id=uuid.uuid4().hex
        self.last_error=None
        for active in pool.workers:
            identity=active['device_id']
            self.states[identity]=dict(active=active,generation=0,busy=0,streak=0,
                                       deadline=None,exhausted=False,incident=None)
        if not self.states: raise ValueError('GPU 서버가 없습니다.')

    def __enter__(self):
        self.evidence=Evidence(self.project)
        self.evidence.__enter__()
        for identity,state in self.states.items(): self._ready(identity,state)
        return self

    def __exit__(self,*args):
        if self.evidence: self.evidence.__exit__(*args)

    def _event(self,identity,device_generation,name,**details):
        if self.evidence:
            details.update(device_id=identity,generation=device_generation,run_id=self.run_id)
            try: self.evidence.event(name,**details)
            except Exception as exc: print('Runtime event log unavailable: '+str(exc),file=sys.stderr,flush=True)

    def _ready(self,identity,state):
        active=state['active'];generation=state['generation']
        active['_runtime_generation']=generation
        active['_recovery_run_id']=self.run_id
        def event(name,**details): self._event(identity,generation,name,**details)
        active['_recovery_event']=event
        if self.evidence:
            def generation_failed(*,request_id,details=None):
                return self.evidence.generation_failed(active,request_id,details)
            active['_recovery_generation_failed']=generation_failed
        self._event(identity,generation,'server_ready',pid=active.get('_runtime_pid'),endpoint=active.get('endpoint'))

    @property
    def capacity(self):
        return sum(int(s['active'].get('worker_slots',s['active'].get('parallel',1)))
                   for s in self.states.values() if s['deadline'] is None and not s['exhausted'])

    @property
    def live(self):
        return any(not s['exhausted'] for s in self.states.values())

    def _sync_recovery_ui(self):
        # A busy healthy GPU still counts as available capacity, not a pause.
        waiting=[s for s in self.states.values() if not s['exhausted'] and s['deadline'] is not None]
        if self.capacity == 0 and self.live and waiting:
            soonest=min(waiting,key=lambda s:s['deadline'])
            deadline=soonest['deadline']
            if deadline == float('inf'): return
            if not self._ui_waiting:
                self.tracker.pause();self._ui_waiting=True
            key=(deadline,soonest['streak'],self.total_attempts)
            if key != self._ui_wait_key:
                delay=max(0,deadline-time.monotonic())
                emit('recovery',state='waiting',delay_seconds=delay,
                     next_retry_at=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat(),
                     attempt=soonest['streak'],total_attempts=self.total_attempts,max_attempts=MAX_RESTARTS)
                self._ui_wait_key=key
        elif self._ui_waiting and self.capacity > 0:
            self.tracker.resume();self._ui_waiting=False;self._ui_wait_key=None
            emit('recovery',state='resuming',delay_seconds=0,next_retry_at=None,
                 attempt=0,total_attempts=self.total_attempts,max_attempts=MAX_RESTARTS)

    def acquire(self, preferred_model=None):
        ids=list(self.states)
        indices=[(self.cursor+offset)%len(ids) for offset in range(len(ids))]
        if preferred_model is not None:
            indices.sort(key=lambda i:self.states[ids[i]]['active'].get('model',self.cfg.get('model')) != preferred_model)
        for index in indices:
            identity=ids[index];state=self.states[identity]
            if state['exhausted'] or state['deadline'] is not None: continue
            active=state['active'];slots=int(active.get('worker_slots',active.get('parallel',1)))
            if state['busy']>=slots: continue
            state['busy']+=1;self.cursor=(index+1)%len(ids)
            result=dict(self.cfg);result.update(active)
            result['_dispatch_device_id']=identity;result['_dispatch_generation']=state['generation']
            return result
        return None

    def release(self,active):
        state=self.states[active['_dispatch_device_id']]
        if active['_dispatch_generation'] != state['generation'] or state['busy']<=0:
            raise ValueError('GPU 서버의 잘못된 작업 해제입니다.')
        state['busy']-=1

    def committed(self,active,item_ids):
        ids=[x for x in item_ids if isinstance(x,str) and x]
        if not ids: return
        identity=active['_dispatch_device_id'];state=self.states[identity]
        if active['_dispatch_generation'] != state['generation']: return
        previous=state['streak'];state['streak']=0
        self._event(identity,state['generation'],'translation_progress_committed',committed_count=len(ids),item_ids=ids[:16])
        if previous:self._event(identity,state['generation'],'restart_streak_reset',previous_restarts=previous)

    def failed(self,active,exc):
        cancelled()
        if not recoverable(exc): raise exc
        identity=active['_dispatch_device_id'];state=self.states[identity]
        if active['_dispatch_generation'] != state['generation'] or state['deadline'] is not None or state['exhausted']:
            return
        self.last_error=exc
        generation=state['generation'];state['deadline']=float('inf')
        self._event(identity,generation,'device_failed',error=repr(exc),saved_entries=self.saved_count())
        if active.get('device_backend')=='rocm':
            self.pool.fallback_after_failure(identity,exc)
        # Close the server immediately: sibling requests will unwind and be released.
        try: self.pool.stop(identity)
        except Exception:
            state['exhausted']=True
            raise
        incident=None
        if self.evidence:
            try: incident=self.evidence.capture(exc,active,state['streak'],self.saved_count())
            except Exception as error:self._event(identity,generation,'collection_failed',error=repr(error))
            if incident:
                try:self.evidence.finalize(incident,active,active.get('_runtime_exit',{}))
                except Exception as error:self._event(identity,generation,'finalization_failed',error=repr(error))
        state['incident']=incident
        if state['streak']>=MAX_RESTARTS:
            state['exhausted']=True;state['deadline']=None
            self._event(identity,generation,'restart_limit',restarts=state['streak'])
            print('GPU 서버 재시도 횟수 초과: '+identity,flush=True)
            if not self.live: raise exc
            self._sync_recovery_ui()
            return
        delay=RESTART_DELAYS[state['streak']];state['streak']+=1;self.total_attempts+=1
        state['deadline']=time.monotonic()+delay
        self._sync_recovery_ui()
        self._event(identity,generation,'restart_scheduled',delay_seconds=delay,restart=state['streak'])
        print(f'GPU 서버 {identity} 재시도 대기 {delay}초.',flush=True)

    def tick(self):
        cancelled()
        now=time.monotonic()
        for identity,state in self.states.items():
            if state['exhausted'] or state['deadline'] is None or state['busy'] or now<state['deadline']: continue
            try: active=self.pool.start(identity)
            except Exception as exc:
                self.last_error=exc
                # A startup failure consumes the same bounded retry budget.
                if state['streak']>=MAX_RESTARTS:
                    state['exhausted']=True;state['deadline']=None
                    self._event(identity,state['generation'],'restart_limit',error=repr(exc))
                else:
                    delay=RESTART_DELAYS[state['streak']];state['streak']+=1;self.total_attempts+=1
                    state['deadline']=time.monotonic()+delay
                    self._event(identity,state['generation'],'restart_scheduled',delay_seconds=delay,error=repr(exc))
                continue
            state['active']=active;state['generation']+=1;state['deadline']=None
            self._ready(identity,state)
        if not self.live and self.last_error: raise self.last_error
        self._sync_recovery_ui()
