"""Small portable entrypoint; Chromium UI and Python CLI share the same tool root."""
import os
import subprocess
from app_paths import ROOT


def main():
    executable = ROOT/'_desktop/RenPyTranslator-UI.exe'
    arguments = []
    import sys
    if not getattr(sys, 'frozen', False):
        executable = ROOT/'desktop/node_modules/electron/dist/electron.exe'
        arguments = [str(ROOT/'desktop')]
    if not executable.is_file():
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk();root.withdraw()
        messagebox.showerror('RenPyTranslator', 'UI 실행 파일이 없습니다. 포터블 폴더 전체를 복사하거나 다시 빌드하세요.')
        root.destroy()
        raise SystemExit(1)
    environment=dict(os.environ,RPT_TOOL_ROOT=str(ROOT))
    environment.pop('ELECTRON_RUN_AS_NODE',None)
    subprocess.Popen([str(executable)]+arguments,cwd=ROOT,env=environment,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
