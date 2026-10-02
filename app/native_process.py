"""Launch native helpers without inheriting the frozen GUI's DLL directory.

The Win32 DLL directory is process-wide. All launches through this module share
one lock, and restore the parent's directory immediately after CreateProcess.
No model, driver or global environment settings are changed.
"""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import threading

_LAUNCH_LOCK = threading.RLock()


def _kernel32():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    return kernel


def _get_directory(kernel):
    ctypes.set_last_error(0)
    needed = kernel.GetDllDirectoryW(0, None)
    if not needed:
        if ctypes.get_last_error(): raise ctypes.WinError(ctypes.get_last_error())
        return None
    buffer = ctypes.create_unicode_buffer(needed + 1)
    ctypes.set_last_error(0)
    written = kernel.GetDllDirectoryW(len(buffer), buffer)
    if not written:
        if ctypes.get_last_error(): raise ctypes.WinError(ctypes.get_last_error())
        return None
    if written >= len(buffer):
        raise RuntimeError('Could not read the parent DLL search directory')
    return buffer.value


def _set_directory(kernel, directory):
    if not kernel.SetDllDirectoryW(directory):
        raise ctypes.WinError(ctypes.get_last_error())


def _inside(path, root):
    if not root or not path: return False
    path = os.path.abspath(os.path.expandvars(path.strip('"')))
    root = os.path.abspath(root)
    try: return os.path.normcase(os.path.commonpath([path, root])) == os.path.normcase(root)
    except ValueError: return False


def clean_environment(env=None, bundle_dir=None):
    result = dict(os.environ if env is None else env)
    removed = []
    if bundle_dir:
        for key in list(result):
            if key.upper() != 'PATH': continue
            entries = result[key].split(os.pathsep)
            removed.extend(entry for entry in entries if _inside(entry, bundle_dir))
            result[key] = os.pathsep.join(entry for entry in entries if not _inside(entry, bundle_dir))
    return result, removed


def native_runtime_directory(executable):
    """Use an app-local redistributable already shipped by this native runtime."""
    base=Path(executable).resolve().parent
    libraries=base/'lib/ollama'
    candidates=[base,libraries]
    if libraries.is_dir():candidates.extend(sorted(p for p in libraries.iterdir() if p.is_dir()))
    for candidate in candidates:
        if all((candidate/name).is_file() for name in ('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll')):
            return str(candidate)
    return None


def popen(args, *, library_directory=None, **kwargs):
    bundle = str(getattr(sys, '_MEIPASS', '') or '') or None
    library_directory=os.fspath(library_directory) if library_directory is not None else None
    if library_directory and (not Path(library_directory).is_dir() or _inside(library_directory,bundle)):
        raise ValueError('Native DLL directory must exist outside the GUI bundle')
    env, removed = clean_environment(kwargs.pop('env', None), bundle)
    if library_directory:
        key=next((key for key in env if key.upper()=='PATH'),'PATH')
        env[key]=library_directory+(os.pathsep+env[key] if env.get(key) else '')
    details = {'policy': 'native_helpers_without_gui_dll_directory',
               'parent_pid': os.getpid(), 'parent_executable': sys.executable,
               'frozen': bool(getattr(sys, 'frozen', False)), 'gui_bundle_directory': bundle,
               'removed_path_entries': removed,
               'executable': os.fspath(args[0]) if not isinstance(args, str) else args,
               'cwd': os.fspath(kwargs.get('cwd') or Path.cwd()),
               'native_library_directory': library_directory,
               'dll_directory_before': None, 'dll_directory_for_child': None,
               'parent_directory_restored': False}
    proc = None
    with _LAUNCH_LOCK:
        kernel = None
        changed = False
        try:
            if os.name == 'nt':
                kernel = _kernel32()
                details['dll_directory_before'] = _get_directory(kernel)
                _set_directory(kernel, None)
                changed = True
            proc = subprocess.Popen(args, env=env, **kwargs)
        except BaseException as exc:
            exc.native_launch = details
            raise
        finally:
            if changed:
                try:
                    _set_directory(kernel, details['dll_directory_before'])
                    details['parent_directory_restored'] = True
                except Exception as exc:
                    # Never leave an unregistered helper running after launch fails.
                    if proc is not None:
                        proc.kill()
                        proc.wait(timeout=10)
                    exc.native_launch = details
                    raise
            else:
                details['parent_directory_restored'] = True
    proc._rpt_native_launch = details
    return proc


def run(args, *, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    if input is not None:
        if kwargs.get('stdin') is not None: raise ValueError('stdin and input may not both be used')
        kwargs['stdin'] = subprocess.PIPE
    if capture_output:
        if kwargs.get('stdout') is not None or kwargs.get('stderr') is not None:
            raise ValueError('stdout/stderr and capture_output may not both be used')
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with popen(args, **kwargs) as proc:
        try:
            stdout, stderr = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            exc.output, exc.stderr = proc.communicate()
            raise
        except BaseException:
            proc.kill()
            raise
        if check and proc.returncode:
            raise subprocess.CalledProcessError(proc.returncode, args, stdout, stderr)
        result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        result._rpt_native_launch = proc._rpt_native_launch
        return result


def self_check(library_directory=None):
    """An OS DLL probe in a native child, with no game or model execution."""
    if os.name != 'nt': return {'skipped': 'Windows-only DLL search check'}
    import base64
    script = """
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class RptNativeDllProbe {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr LoadLibraryW(string path);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetModuleFileNameW(IntPtr module, StringBuilder text, int size);
}
'@
$h=[RptNativeDllProbe]::LoadLibraryW('VCRUNTIME140.dll')
if ($h -eq [IntPtr]::Zero) { throw 'Could not load VCRUNTIME140.dll in the native probe' }
$s=[Text.StringBuilder]::new(32768)
if ([RptNativeDllProbe]::GetModuleFileNameW($h,$s,$s.Capacity) -eq 0) { throw 'Could not read the loaded DLL path' }
$path=$s.ToString()
[pscustomobject]@{dll_path=$path;version=([Diagnostics.FileVersionInfo]::GetVersionInfo($path)).FileVersion;first_path_entry=($env:PATH -split ';')[0]} | ConvertTo-Json -Compress
"""
    shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    encoded = base64.b64encode(script.encode('utf-16-le')).decode()
    result = run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                 library_directory=library_directory,
                 capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
    import json
    record = json.loads(result.stdout)
    record['native_launch'] = result._rpt_native_launch
    if _inside(record['dll_path'], result._rpt_native_launch['gui_bundle_directory']):
        raise RuntimeError('Native child still loaded a GUI DLL: ' + record['dll_path'])
    return record
