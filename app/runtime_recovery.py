"""Bounded recovery of the tool-owned translation server, with local evidence.

No game reads, inference probes, driver changes, or changes to model settings.
The translation parent is the only cache writer and the only recovery owner.
"""
from collections import deque
from datetime import datetime, timezone, timedelta
import base64
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import uuid

MAX_RESTARTS=2
HIDDEN=getattr(subprocess,'CREATE_NO_WINDOW',0)


def cancelled():
    path=os.environ.get('RPT_CANCEL_FILE')
    if path and Path(path).exists():raise KeyboardInterrupt


def recoverable(exc):
    if not getattr(exc,'_rpt_transport',False):return False
    return not isinstance(exc,urllib.error.HTTPError) or exc.code in (500,502,503,504)


def command(args,timeout=4):
    try:
        from native_process import run as native_run
        p=native_run(args,capture_output=True,encoding='utf-8',errors='replace',
                         timeout=timeout,creationflags=HIDDEN)
        return {'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    except Exception as exc:return {'collection_error':repr(exc)}


def powershell(script,timeout=6):
    encoded=base64.b64encode(('[Console]::OutputEncoding=[Text.Encoding]::UTF8; '+script).encode('utf-16-le')).decode()
    return command(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],timeout)


def resources():
    sample={'time':datetime.now(timezone.utc).isoformat()}
    sample['gpu']=command(['nvidia-smi','--query-gpu=index,uuid,name,driver_version,pstate,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu,power.draw,clocks.sm,clocks.mem',
                           '--format=csv'])
    if os.name=='nt':
        try:
            class Memory(ctypes.Structure):
                _fields_=[('length',ctypes.c_ulong),('load_percent',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in
                    ('physical_total','physical_free','commit_limit','commit_available','virtual_total','virtual_free','extended_free')]
            m=Memory();m.length=ctypes.sizeof(m)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):raise ctypes.WinError()
            sample['system_memory']={n:getattr(m,n) for n,_ in m._fields_ if n!='length'}
        except Exception as exc:sample['memory_collection_error']=repr(exc)
    return sample


def process_snapshot(pid):
    return powershell(
        "Get-CimInstance Win32_Process -Filter 'ProcessId = "+str(int(pid))+" OR ParentProcessId = "+str(int(pid))+"' | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,WorkingSetSize,PageFileUsage,PeakPageFileUsage,HandleCount,ThreadCount | ConvertTo-Json -Depth 3")


def module_snapshot(pid,bundle_directory=None):
    """Only our Ollama process and its runner; keep loaded paths before a crash."""
    result={'time':datetime.now(timezone.utc).isoformat(),'runtime_pid':int(pid)}
    probe=powershell(
        "$ErrorActionPreference='Stop'; $out=@(Get-CimInstance Win32_Process -Filter 'ProcessId = "+str(int(pid))+
        " OR ParentProcessId = "+str(int(pid))+"' | Where-Object {$_.Name -in @('ollama.exe','llama-server.exe')} | ForEach-Object { "
        "$p=$_; $errorText=$null; $mods=@(); try { $g=Get-Process -Id $p.ProcessId -ErrorAction Stop; "
        r"$mods=@($g.Modules | Where-Object {$_.ModuleName -match '^(ggml.*|libllama.*|llama-server|VCRUNTIME.*|MSVCP.*|ucrtbase|cublas.*|cudart.*|nvcuda.*|libomp.*|libc\+\+.*|libunwind.*)\.(dll|exe)$'} | "
        "ForEach-Object {[pscustomobject]@{name=$_.ModuleName;path=$_.FileName;version=$_.FileVersionInfo.FileVersion;product_version=$_.FileVersionInfo.ProductVersion}}) "
        "} catch {$errorText=$_.Exception.Message}; [pscustomobject]@{pid=$p.ProcessId;parent_pid=$p.ParentProcessId;executable=$p.ExecutablePath;modules=$mods;collection_error=$errorText} "
        "}); ConvertTo-Json -InputObject $out -Depth 5 -Compress",timeout=8)
    try:
        if probe.get('returncode')!=0:raise RuntimeError(str(probe))
        processes=json.loads(probe.get('stdout') or '[]')
        if isinstance(processes,dict):processes=[processes]
        result['processes']=processes
        from native_process import _inside
        result['gui_bundle_modules']=[dict(pid=p['pid'],**m) for p in processes for m in p.get('modules',[]) or []
                                      if _inside(m.get('path',''),bundle_directory)]
        result['runner_present']=any(Path(p.get('executable') or '').name.lower()=='llama-server.exe' for p in processes)
        if result['gui_bundle_modules']:
            result['warning']='The native model process loaded libraries from the GUI bundle; cause of any GPU fault is not established.'
    except Exception as exc:
        result.update(collection_error=repr(exc),probe=probe)
    return result


class Evidence:
    def __init__(self,project):
        self.directory=Path(project)/'data/errors'
        self.history=deque(maxlen=150)  # approximately five minutes, bounded RAM
        self.lock=threading.Lock();self.stop=threading.Event()
        self.thread=threading.Thread(target=self._sample,daemon=True,name='rpt-runtime-evidence')
        self.incidents=[]
        self.runtime_pid=0
        self.runtime_launch={}
        self.module_history=deque(maxlen=8)
        self.seen_runners=set()

    def configure(self,cfg):
        with self.lock:
            self.runtime_pid=cfg.get('_runtime_pid',0)
            self.runtime_launch=cfg.get('_runtime_launch',{})

    def __enter__(self):self.thread.start();return self

    def __exit__(self,*args):self.stop.set();self.thread.join(timeout=12)

    def _sample(self):
        last_process=0
        last_error=None
        while not self.stop.is_set():
            try:
                sample=resources()
                with self.lock:
                    pid=self.runtime_pid;launch=dict(self.runtime_launch)
                if os.name=='nt' and pid and time.monotonic()-last_process>=10:
                    sample['server_processes']=process_snapshot(pid)
                    last_process=time.monotonic()
                    try:
                        processes=json.loads(sample['server_processes'].get('stdout') or '[]')
                        if isinstance(processes,dict):processes=[processes]
                        runners={int(p['ProcessId']) for p in processes if Path(p.get('ExecutablePath') or '').name.lower()=='llama-server.exe'}
                        new={(pid,runner) for runner in runners}-self.seen_runners
                        if new:
                            self.seen_runners.update(new)
                            inventory=module_snapshot(pid,launch.get('gui_bundle_directory'))
                            with self.lock:self.module_history.append(inventory)
                            self.event('runtime_modules',snapshot=inventory)
                    except Exception as exc:
                        sample['module_detection_error']=repr(exc)
                with self.lock:self.history.append(sample)
                last_error=None
            except Exception as exc:
                error=repr(exc)
                with self.lock:self.history.append({'time':datetime.now(timezone.utc).isoformat(),'collection_error':error})
                if error!=last_error:self.event('sampling_failed',error=error)
                last_error=error
            self.stop.wait(2)

    def event(self,name,**details):
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            with (self.directory/'runtime-recovery.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(dict(event=name,time=datetime.now(timezone.utc).isoformat(),**details),ensure_ascii=False)+'\n')
                f.flush()
        except Exception as exc:print('Runtime event log unavailable: '+str(exc),file=sys.stderr,flush=True)

    def capture(self,exc,cfg,attempt,saved):
        incident=datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:10]
        record={'incident_id':incident,'time':datetime.now(timezone.utc).isoformat(),
            'attempt':attempt,'error':repr(exc),'http':getattr(exc,'http_details',None),
            'error_file':getattr(exc,'_rpt_error_file',None),'request_id':getattr(exc,'request_id',None),
            'request_trace':str(self.directory.parent/'translation-requests.jsonl'),
            'recovery_state':getattr(exc,'recovery_state',{}),'saved_entries':saved,
            'settings':{k:cfg.get(k) for k in ('model','num_ctx','parallel','batch_size','endpoint','_runtime_pid','_runtime_env','_runtime_log','_runtime_launch')},
            'collection_errors':[]}
        with self.lock:
            record['resource_history']=list(self.history)
            record['module_history']=list(self.module_history)
        self.directory.mkdir(parents=True,exist_ok=True)
        stem=self.directory/('runtime-'+incident)
        log=cfg.get('_runtime_log')
        if log:
            try:
                with Path(log).open('rb') as f:
                    record['server_startup']=f.read(65536).decode('utf-8','replace')
                    f.seek(0,2);size=f.tell();f.seek(max(0,size-524288))
                    tail=stem.with_suffix('.server.log');tail.write_bytes(f.read())
                record.update(server_tail=str(tail),server_bytes=size)
            except Exception as e:record['collection_errors'].append('server log: '+repr(e))
        # Persist the essential evidence before slower OS probes.
        path=stem.with_suffix('.json')
        def write():
            temp=path.with_suffix('.json.tmp')
            temp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
        write()
        self.incidents.append(incident)
        self.event('incident',incident_id=incident,path=str(path),saved_entries=saved)
        try:
            record['resources_at_failure']=resources()
            record['gpu_details']=command(['nvidia-smi','-q','-x'])
            from hy_backend import api
            for route in ('/api/version','/api/ps'):
                try:record[route]=api(cfg['endpoint'],route)
                except Exception as e:record[route]={'collection_error':repr(e)}
            record['gpu_processes']=command(['nvidia-smi','--query-compute-apps=pid,process_name,used_gpu_memory','--format=csv'])
            if os.name=='nt':
                start=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
                record['windows_events']=powershell(
                    "$s=[datetimeoffset]'"+start+"'; Get-WinEvent -FilterHashtable @{LogName=@('System','Application');StartTime=$s.LocalDateTime;Level=@(1,2,3)} -ErrorAction SilentlyContinue | "
                    "Where-Object {$_.ProviderName -in @('nvlddmkm','Display','Microsoft-Windows-WHEA-Logger','Microsoft-Windows-Resource-Exhaustion-Detector') -or $_.Message -match 'llama-server|ollama.exe'} | "
                    "Select-Object -First 50 | ForEach-Object {$_.ToXml()}")
                pid=int(cfg.get('_runtime_pid') or 0)
                if pid:
                    record['server_processes']=process_snapshot(pid)
                    record['modules_at_failure']=module_snapshot(pid,cfg.get('_runtime_launch',{}).get('gui_bundle_directory'))
                record['windows_crash_reports']=powershell(
                    "$s=[datetimeoffset]'"+start+"'; Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=$s.LocalDateTime;Id=1001} -ErrorAction SilentlyContinue | "
                    "Where-Object {$_.ProviderName -eq 'Windows Error Reporting' -and $_.Message -match 'llama-server|ollama.exe'} | Select-Object -First 10 | ForEach-Object {$_.ToXml()}")
        except Exception as e:record['collection_errors'].append(repr(e))
        write()
        print('Runtime incident: '+str(path),flush=True)
        return incident


def run(project,cfg,operation,saved_count):
    from hy_backend import runtime
    with Evidence(project) as evidence:
        restarted=0;incident=None
        while True:
            cancelled()
            failure=None
            try:
                with runtime(cfg) as active:
                    evidence.configure(active)
                    active=dict(active,_recovery_event=evidence.event,_recovery_incident=incident)
                    evidence.event('server_ready',incident_id=incident,restart=restarted,
                        pid=active.get('_runtime_pid'),endpoint=active.get('endpoint'),server_log=active.get('_runtime_log'),native_launch=active.get('_runtime_launch'))
                    cancelled()
                    try:
                        result=operation(active)
                    except Exception as exc:
                        cancelled()
                        if not recoverable(exc):raise
                        failure=exc
                        try:incident=evidence.capture(exc,active,restarted,saved_count())
                        except Exception as log_error:
                            evidence.event('collection_failed',error=repr(log_error),original_error=repr(exc))
                            print('Runtime diagnostics incomplete: '+str(log_error),file=sys.stderr,flush=True)
                        raise
                    else:
                        if restarted:
                            evidence.event('resume_completed',incident_id=incident,restarts=restarted,saved_entries=saved_count())
                            evidence.event('resources_after_resume',incident_id=incident,snapshot=resources())
                        return result
            except Exception as exc:
                cancelled()
                # A cleanup failure must never start a second model server.
                if failure is None or exc is not failure:
                    evidence.event('stopped',incident_id=incident,error=repr(exc),reason='nonrecoverable_or_cleanup_failed')
                    raise
                evidence.event('server_stopped',incident_id=incident,saved_entries=saved_count())
                evidence.runtime_pid=0
                # Runtime.__exit__ has killed the old process tree. Let all old
                # request workers exit before any replacement is created.
                workers=getattr(exc,'_rpt_workers',[])
                if workers:
                    from concurrent.futures import wait
                    _,pending=wait(workers,timeout=5)
                    if pending:
                        evidence.event('stopped',incident_id=incident,reason='old_request_workers_still_running')
                        raise
                if restarted>=MAX_RESTARTS:
                    evidence.event('restart_limit',incident_id=incident,restarts=restarted)
                    print('Automatic recovery stopped after 2 restarts. Saved translations are retained.',flush=True)
                    raise
                restarted+=1
                evidence.event('restart_scheduled',incident_id=incident,restart=restarted,saved_entries=saved_count())
                print(f'Model server failed. Restarting ({restarted}/{MAX_RESTARTS}); saved translations will be skipped.',flush=True)
                for _ in range(10):cancelled();time.sleep(.1)
