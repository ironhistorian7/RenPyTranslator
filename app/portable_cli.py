"""Executable/legacy CLI entrypoint; isolated helper commands use the bundled Python."""
import sys
import os
import threading
import time
from pathlib import Path


class SerialPool:
    def __init__(self,*args,**kwargs):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def imap(self,func,items,*args):return map(func,items)


def cancel_watch():
    path=os.environ.get('RPT_CANCEL_FILE')
    if not path:return
    def watch():
        while not Path(path).exists():time.sleep(.2)
        from cancel_runtime import stop_children
        stop_children()
        import _thread
        _thread.interrupt_main()
    threading.Thread(target=watch,daemon=True).start()


def main():
    for stream in (sys.stdout,sys.stderr):
        if stream is not None and hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace',line_buffering=True)
    if (getattr(sys,'frozen',False) and Path(sys.executable).name=='RenPyTranslator.exe' and len(sys.argv)==1) or sys.argv[1:]==['--gui']:
        from desktop_launcher import main as gui_main
        gui_main();return
    if sys.argv[1:] == ['--desktop-info']:
        import json
        from desktop_bridge import snapshot
        print(json.dumps(snapshot(),ensure_ascii=False));return
    if sys.argv[1:] == ['--desktop-settings']:
        import json
        from desktop_bridge import read_request,save_settings
        print(json.dumps(save_settings(read_request()),ensure_ascii=False));return
    if sys.argv[1:] == ['--desktop-project']:
        import json
        from desktop_bridge import project_language,read_request
        print(json.dumps(project_language(read_request()),ensure_ascii=False));return
    if sys.argv[1:] == ['--desktop-models']:
        import json
        from desktop_bridge import read_request
        from model_store import dispatch
        cancel_watch()
        try:print(json.dumps({'result':dispatch(read_request())},ensure_ascii=False),flush=True)
        except KeyboardInterrupt:print(json.dumps({'cancelled':True}),flush=True);raise SystemExit(130)
        except Exception as exc:
            from diagnostics import report
            report(exc);raise SystemExit(1)
        return
    if len(sys.argv)>1 and sys.argv[1]=='--unrpyc':
        from app_paths import ROOT
        sys.path.insert(0,str(ROOT/'vendor/unrpyc'))
        import unrpyc
        unrpyc.Pool=SerialPool
        sys.argv=[sys.argv[0]]+sys.argv[2:]
        unrpyc.main();return
    if len(sys.argv)>1 and sys.argv[1]=='--self-check':
        from app_paths import ROOT
        import tkinter
        from fontTools.ttLib import TTFont
        import translation,packaging,failed_repair,desktop_bridge,desktop_launcher,runtime_recovery,native_process
        import json
        from runtime_assets import runtime_executable
        ollama = runtime_executable('ollama', ROOT)
        assert ollama.is_file(), 'Bundled Ollama executable is missing'
        print('Native DLL isolation: '+json.dumps(native_process.self_check(
            native_process.native_runtime_directory(ollama)),ensure_ascii=False))
        print('Python: bundled' if getattr(sys,'frozen',False) else 'Python: tool venv')
        print('Python libraries, desktop bridge and CLI imports OK')
        print('Root: '+str(ROOT))
        print('Ollama executable present: '+str(ollama.is_file()))
        font=ROOT/'vendor/fonts/NanumSquareNeo-Regular.ttf'
        if font.exists():
            with TTFont(font,lazy=True) as face:print('Bundled Korean font readable: '+str(ord('\uac00') in face.getBestCmap()))
        ui=ROOT/'_desktop'
        if getattr(sys,'frozen',False):
            assert (ui/'RenPyTranslator-UI.exe').is_file(), 'Electron executable is missing'
            for name in ('main.js','preload.js','renderer.js','index.html','style.css'):
                assert (ui/'resources/app'/name).is_file(), 'UI asset is missing: '+name
            print('Bundled Electron UI assets present.')
        print('No game or inference started.');return
    cancel_watch()
    if sys.argv[1:] == ['--desktop-run']:
        from desktop_bridge import run_request,read_request
        cli=lambda:run_request(read_request())
    else:
        from translate_game import main as cli
    try:cli()
    except KeyboardInterrupt:
        print('Cancelled. Saved results are retained.',file=sys.stderr);raise SystemExit(130)
    except Exception as exc:
        if os.environ.get('RPT_CANCEL_FILE') and Path(os.environ['RPT_CANCEL_FILE']).exists():
            print('Cancelled. Saved results are retained.',file=sys.stderr);raise SystemExit(130)
        from diagnostics import report
        report(exc);raise SystemExit(1)


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
