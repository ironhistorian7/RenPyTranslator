"""Optional desktop progress events; estimates use committed work and wall time."""
import json
import math
import os
import sys
import threading
import time


_LOCK=threading.RLock()
_active=None


class _Rate:
    def __init__(self):
        self.started=self.last=time.monotonic();self.work=0;self.rate=None;self.paused=None

    def pause(self):
        if self.paused is None:self.paused=time.monotonic()

    def resume(self):
        if self.paused is not None:
            duration=time.monotonic()-self.paused
            self.started+=duration;self.last+=duration;self.paused=None

    def estimate(self,completed,total):
        if total is not None and completed>=total:return 0.0
        if self.paused is not None:return None
        now=time.monotonic();duration=now-self.last;delta=completed-self.work
        # Aggregate close worker completions rather than treating milliseconds as
        # a fresh throughput sample. Failed/repeated requests add no work units.
        if duration>=1 and delta>0:
            speed=delta/duration
            alpha=1-math.exp(-duration/15)
            self.rate=speed if self.rate is None else self.rate*(1-alpha)+speed*alpha
            self.last=now;self.work=completed
        if total is None or now-self.started<2 or not self.rate:return None
        return max(0.0,(total-completed)/self.rate)


class _Phase:
    def __init__(self,phase,label,total,unit):
        self.phase=phase;self.label=label;self.total=total;self.unit=unit
        self.started=time.monotonic();self.completed=0;self.rate=_Rate()
        self.last_progress=None


def emit(event,**fields):
    """One machine-readable line when requested; ordinary CLI output is untouched."""
    if os.environ.get('RPT_DESKTOP_EVENTS')!='1':return
    with _LOCK:
        if _active is not None:
            fields.setdefault('phase',_active.phase);fields.setdefault('label',_active.label)
            fields.setdefault('elapsed_seconds',round(time.monotonic()-_active.started,3))
        sys.stdout.write('RPT_EVENT '+json.dumps(dict(event=event,**fields),ensure_ascii=False,default=str)+'\n')
        sys.stdout.flush()


def set_phase(phase,label,total=None,unit='items'):
    """A new phase gets fresh counters; re-entering the current phase preserves it."""
    global _active
    with _LOCK:
        if _active is None or _active.phase!=phase:
            _active=_Phase(phase,label,total,unit)
            emit('stage',phase=phase,label=label,completed=0,total=total,unit=unit,eta_seconds=None)
        else:
            _active.label=label;_active.unit=unit
            if total is not None:_active.total=total
        return _active


def progress(phase,completed,total,**counts):
    with _LOCK:
        label=counts.pop('label',_active.label if _active is not None and _active.phase==phase else phase)
        unit=counts.pop('unit',_active.unit if _active is not None and _active.phase==phase else 'items')
        state=set_phase(phase,label,total,unit)
        state.completed=completed
        eta=counts.pop('eta_seconds') if 'eta_seconds' in counts else state.rate.estimate(completed,total)
        now=time.monotonic()
        finished=total is not None and completed>=total
        if not finished and state.last_progress is not None and now-state.last_progress<.2:return
        state.last_progress=now
        emit('progress',phase=phase,label=label,completed=completed,total=total,unit=unit,
             eta_seconds=round(eta,1) if eta is not None else None,**counts)


class TranslationProgress:
    """One pending-ID denominator across server generations of a translation phase."""
    def __init__(self):
        self.units=None;self.records={};self.cached=0;self.rate=None;self.state=None
        self.total_work=0;self.completed_work=0;self.original_kept=0

    def _settle(self,items):
        for item in items:
            uid=item['id']
            if uid not in self.units:continue
            original=item.get('status')=='source_fallback'
            if uid not in self.records:
                self.completed_work+=self.units[uid];self.original_kept+=original
            else:self.original_kept+=original-self.records[uid]
            self.records[uid]=original

    def begin(self,rows,pending,known,units):
        if self.units is None:
            self.units=units
            self.total_work=sum(units.values())
            self.cached=sum(row['id'] in known for row in rows)
            self.rate=_Rate()
        self.state=set_phase('translation','번역',len(self.units))
        # A saved sibling may have committed during runtime teardown. Reconcile
        # its exact ID, without counting pre-existing cache or a retry twice.
        self._settle(dict(known[uid],id=uid) for uid in self.units if uid in known)
        self.publish()

    def pause(self):
        if self.rate is not None:self.rate.pause()

    def resume(self):
        if self.rate is not None:self.rate.resume()

    def commit(self,result):
        self._settle(result)
        self.publish()

    def snapshot(self):
        return dict(completed=len(self.records),pending_at_start=len(self.units),
                    seconds=round(time.monotonic()-self.state.started),
                    cached=self.cached,successful=len(self.records)-self.original_kept,
                    original_kept=self.original_kept)

    def publish(self):
        snapshot=self.snapshot();done=self.completed_work;total=self.total_work
        progress('translation',snapshot['completed'],snapshot['pending_at_start'],
                 cached=snapshot['cached'],successful=snapshot['successful'],original_kept=snapshot['original_kept'],
                 work_completed=done,work_total=total,rate_unit='source_units',eta_seconds=self.rate.estimate(done,total))


def translation_tracker(cfg):
    """Keep the shared object private and transient; it is never a cache record."""
    tracker=cfg.get('_job_translation_progress')
    if not isinstance(tracker,TranslationProgress):
        tracker=TranslationProgress();cfg['_job_translation_progress']=tracker
    return tracker
