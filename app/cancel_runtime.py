"""Only child processes owned by the current CLI can be stopped by GUI cancel."""
import subprocess
import threading

_children=set()
_lock=threading.Lock()

def register(proc):
    with _lock:_children.add(proc)

def unregister(proc):
    with _lock:_children.discard(proc)

def stop_children():
    with _lock:children=list(_children)
    for proc in children:
        if proc.poll() is None:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=15,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
