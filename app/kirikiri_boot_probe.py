"""Explicitly invoked, bounded launch observation. No probes occur on import.

Windows-only. A visible window is never considered initialization readiness.
The optional readiness callback receives a snapshot and must return a mapping
with ready=True and nonempty evidence, based on caller-owned strong observation.
"""
from __future__ import annotations
import base64
import binascii
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:
    from portable_locale import pe_architecture, runtime_dir
    from native_process import popen as native_popen, run as native_run
except ImportError:
    from .portable_locale import pe_architecture, runtime_dir
    from .native_process import popen as native_popen, run as native_run


def _now():
    return datetime.now(timezone.utc).isoformat()


def _candidate(text):
    """Classify narrowly worded conversion errors; retain raw and decoded proof."""
    if not text:
        return None
    patterns = (
        (r'\b(?:invalid|illegal)\s+(?:multi[ -]?byte|byte sequence)', 'invalid_multibyte'),
        (r'\b(?:cannot|failed to|unable to)\s+(?:encode|decode|convert)\b.{0,100}\b(?:path|filename|file name|unicode|character|encoding)', 'filepath_conversion_error'),
        (r'\bno mapping for the unicode character exists in the target multi-byte code page\b', 'invalid_multibyte'),
        (r'(?:unicode.{0,20}(?:ansi|multi[ -]?byte)|(?:ansi|multi[ -]?byte).{0,20}unicode).{0,40}(?:fail|error|invalid|cannot)', 'encoding_error'),
        (r'(?:文字列|ファイル名|パス|文字コード|Unicode|マルチバイト).{0,60}(?:変換できません|変換できない|変換に失敗|不正|無効)', 'encoding_error'),
        (r'(?:変換できません|変換に失敗).{0,60}(?:文字列|ファイル名|パス|文字コード|Unicode|マルチバイト)', 'encoding_error'),
    )
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        if re.search(r'\b(?:syntaxerror|syntax error|cuda|traceback)\b', line, re.I):
            continue
        variants = [(line, None)]
        try:
            raw = line.encode('cp949', errors='strict')
            decoded = raw.decode('cp932', errors='strict')
            if decoded != line and decoded.encode('cp932', errors='strict').decode('cp949', errors='strict') == line:
                variants.append((decoded, 'cp949_bytes_as_cp932'))
        except UnicodeError:
            pass
        for candidate, method in variants:
            for pattern, code in patterns:
                if re.search(pattern, candidate, re.I):
                    result = {'code': code, 'proof': candidate[:300]}
                    if method:
                        result.update({'raw_proof':line[:300], 'decoded_from':method})
                    return result
    return None


def _msaa_dialog_text(hwnd, pid):
    """Ask a bounded helper for static/text MSAA children of this error form."""
    if getattr(sys, 'frozen', False):
        root = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    else:
        root = Path(__file__).resolve().parent.parent
    helper = root / 'runtimes' / 'probe' / 'MsaaProbe.exe'
    if not helper.is_file():
        return [], 'helper_unavailable'
    try:
        run = native_run([str(helper), str(hwnd), str(pid)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=1, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        return [], 'timeout'
    except OSError:
        return [], 'helper_error'
    if run.returncode:
        return [], 'helper_error'
    values = []
    for line in run.stdout[:65536].splitlines()[:128]:
        try:
            value = base64.b64decode(line, validate=True).decode('utf-8')
        except (binascii.Error, UnicodeError, ValueError):
            continue
        if value and value not in values:
            values.append(value[:1024])
    return values, 'ok' if values else 'no_accessible_text'


def _windows(pid, msaa_cache=None):
    """Read titles and dialog static text belonging to this PID only."""
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    windows = []
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.EnumChildWindows.argtypes = (wintypes.HWND, callback_type, wintypes.LPARAM)
    user32.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    def get_text(hwnd):
        buf = ctypes.create_unicode_buffer(1025)
        user32.GetWindowTextW(hwnd, buf, len(buf))
        return buf.value
    def get_child_text(hwnd):
        text = get_text(hwnd)
        if text:
            return text
        # WM_GETTEXT is marshalled by the OS for standard system messages.
        # Bound the wait for an unresponsive owned child window.
        buf = ctypes.create_unicode_buffer(1025)
        result = ctypes.c_size_t()
        ok = user32.SendMessageTimeoutW(hwnd, 0x000D, len(buf), ctypes.cast(buf, ctypes.c_void_p).value,
                                        0x0002, 150, ctypes.byref(result))
        return buf.value if ok else ''
    def get_class(hwnd):
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buf, len(buf))
        return buf.value
    @callback_type
    def collect(hwnd, param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        cls = get_class(hwnd)
        item = {'handle': int(hwnd), 'class': cls, 'title': get_text(hwnd), 'dialog_text': []}
        if cls.lower() in {'#32770', 'tmessageform'}:
            @callback_type
            def child(child_hwnd, unused):
                child_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(child_hwnd, ctypes.byref(child_pid))
                child_class = get_class(child_hwnd).lower()
                allowed = child_class in {'static', 'edit', 'tlabel', 'tstatictext', 'tmemo', 'trichedit'} or child_class.startswith('richedit')
                if child_pid.value == pid and allowed:
                    value = get_child_text(child_hwnd)
                    if value: item['dialog_text'].append(value[:1024])
                return True
            user32.EnumChildWindows(hwnd, child, 0)
            if cls.lower() == 'tmessageform' and not item['dialog_text']:
                key = int(hwnd)
                cached = msaa_cache.get(key) if msaa_cache is not None else None
                if cached is None or time.monotonic() - cached[0] >= 2:
                    values, status = _msaa_dialog_text(key, pid)
                    cached = (time.monotonic(), values, status)
                    if msaa_cache is not None: msaa_cache[key] = cached
                item['dialog_text'] = list(cached[1])
                item['msaa_status'] = cached[2]
        windows.append(item)
        return True
    user32.EnumWindows(collect, 0)
    return windows


def _open_process(pid):
    k = ctypes.windll.kernel32
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.OpenProcess.restype = wintypes.HANDLE
    # Query and synchronize only; terminate is requested separately only if opted in.
    return k.OpenProcess(0x1000 | 0x00100000, False, pid)


def _process_code(handle):
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
        return None
    return None if code.value == 259 else code.value


def probe_game(exe, mode, runtime=None, timeout=12, readiness=None, terminate_owned=False):
    """Run and observe exactly one explicitly named EXE; no readiness inference.

    readiness(snapshot) returns {'ready': True, 'evidence': <nonempty value>}
    only upon independently observed initialization. A callback failure is
    reported as unknown. stdout/stderr capture is native-only; Japanese child
    streams are not captured by the upstream launcher API. Data remains in a
    newly created temporary directory, returned via log_directory.
    """
    import os
    if os.name != 'nt': raise OSError('Windows is required')
    if mode not in ('native', 'japanese'): raise ValueError('mode must be native or japanese')
    exe = Path(exe).resolve(strict=True)
    if not exe.is_file() or exe.suffix.lower() != '.exe': raise ValueError('exe must be an executable file')
    if not 0 < timeout <= 300: raise ValueError('timeout must be between 0 and 300 seconds')
    if mode == 'japanese' and pe_architecture(exe) != 'x86': raise ValueError('Japanese locale requires x86')
    directory = Path(tempfile.mkdtemp(prefix='kirikiri-boot-probe-'))
    stdout_path, stderr_path = directory/'stdout.log', directory/'stderr.log'
    pid_path = directory/'child.pid'
    if mode == 'japanese':
        runtime = Path(runtime) if runtime is not None else runtime_dir()
        for name in ('Play-Korean.exe', 'LoaderDll.dll', 'LocaleEmulator.dll'):
            shutil.copy2(runtime/name, directory/name)
        command = [str(directory/'Play-Korean.exe'), '--probe', str(exe), str(pid_path)]
    else:
        command = [str(exe)]
    system_acp = ctypes.windll.kernel32.GetACP()
    report = {'mode':mode, 'started_at':_now(), 'finished_at':None,
        'code_page': None, 'system_acp':system_acp,
        'code_page_basis': 'not measured in target; system ACP=%s; %s' % (system_acp, 'Japanese hook configured for 932' if mode=='japanese' else 'native environment'),
        'configured_code_page':932 if mode=='japanese' else None,
        'pid':None, 'launcher_pid':None, 'returncode':None, 'outcome':'unknown',
        'target_active':None, 'termination_requested':bool(terminate_owned), 'cleanup_error':None,
        'error':None, 'windows':[], 'readiness_evidence':None,
        'log_directory':str(directory), 'stdout_log':str(stdout_path), 'stderr_log':str(stderr_path),
        'log_scope':'native process stdout/stderr' if mode=='native' else 'launcher only; target stdout/stderr unavailable'}
    ctypes.windll.kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    ctypes.windll.kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    process = None; target_handle = None; msaa_cache = {}
    with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
        try:
            process = native_popen(command, cwd=str(exe.parent), stdout=stdout, stderr=stderr,
                                   library_directory=(directory if mode == 'japanese' else exe.parent))
            report['launcher_pid'] = process.pid if mode=='japanese' else None
            if mode=='native': report['pid'] = process.pid
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if mode=='japanese' and report['pid'] is None and pid_path.is_file():
                    try:
                        pid = int(pid_path.read_text(encoding='ascii').strip())
                        target_handle = _open_process(pid)
                        if target_handle:
                            report['pid'] = pid
                        else:
                            report['untracked_pid'] = pid
                    except (OSError, ValueError): pass
                if report['pid'] is not None:
                    report['windows'] = _windows(report['pid'], msaa_cache)
                    texts = [x for w in report['windows'] if w['class'].lower() in {'#32770', 'tmessageform'} for x in w['dialog_text']]
                    for text in texts:
                        error = _candidate(text)
                        if error:
                            error.update({'source':'owned_dialog', 'source_pid':report['pid'], 'observed_at':_now()})
                            report['error'] = error; report['outcome'] = 'initialization_failed'; break
                    if report['error']: break
                    if readiness:
                        try:
                            check = readiness(dict(report))
                            if isinstance(check, dict) and check.get('ready') is True and check.get('evidence'):
                                report['readiness_evidence'] = check['evidence']; report['outcome'] = 'initialization_ready'; break
                        except Exception as exc:
                            report['readiness_error'] = str(exc)[:300]; break
                    code = process.poll() if mode=='native' else (_process_code(target_handle) if target_handle else None)
                    if code is not None:
                        report['returncode'] = code
                        if code != 0: report['outcome'] = 'initialization_failed'
                        break
                elif mode=='japanese' and process.poll() is not None:
                    report['launcher_returncode'] = process.returncode
                    error_path = Path(str(pid_path)+'.error')
                    if error_path.exists(): report['launcher_error'] = error_path.read_text(encoding='utf-8-sig', errors='replace')[:300]
                    break
                time.sleep(.2)
            stdout.flush(); stderr.flush()
            if mode=='native' and report['outcome'] == 'initialization_failed' and not report['error']:
                for path in (stderr_path, stdout_path):
                    text = path.read_bytes()[:65536].decode('utf-8', errors='replace')
                    error = _candidate(text)
                    if error:
                        error.update({'source':path.name, 'source_pid':report['pid'], 'observed_at':_now()}); report['error']=error; break
        finally:
            if process is not None:
                try:
                    if terminate_owned:
                        if mode == 'native' and process.poll() is None:
                            process.terminate()
                            try: process.wait(timeout=3)
                            except subprocess.TimeoutExpired: pass
                        elif mode == 'japanese' and target_handle and _process_code(target_handle) is None:
                            # Keep the query handle open throughout PID-based termination.
                            h = ctypes.windll.kernel32.OpenProcess(0x0001, False, report['pid'])
                            if h:
                                try: ctypes.windll.kernel32.TerminateProcess(h, 1)
                                finally: ctypes.windll.kernel32.CloseHandle(h)
                        if mode == 'japanese' and process.poll() is None:
                            process.terminate()
                    if mode == 'native':
                        report['target_active'] = process.poll() is None
                    elif target_handle:
                        report['target_active'] = _process_code(target_handle) is None
                except (OSError, subprocess.SubprocessError) as exc:
                    report['cleanup_error'] = str(exc)[:300]
                finally:
                    if target_handle: ctypes.windll.kernel32.CloseHandle(target_handle)
            report['finished_at'] = _now()
    return report


def probe_locale_requirement(exe, runtime=None, timeout=12, readiness=None, terminate_owned=False):
    """Explicitly observe native, then JA only for a PID-bound locale error.

    This launches programs. The caller must supply a strong readiness callback;
    visible windows do not qualify. Cleanup is opt-in. If the native target is
    still active or cannot be proven stopped, a second launch is not attempted.
    This returns the underlying reports, preserving unmeasured ACP as None.
    """
    try:
        from kirikiri_locale_probe import compare_boot_results
    except ImportError:
        from .kirikiri_locale_probe import compare_boot_results
    native = probe_game(exe, 'native', runtime=runtime, timeout=timeout,
                        readiness=readiness, terminate_owned=terminate_owned)
    decision = compare_boot_results(native)
    result = {'status': decision['status'], 'locale': decision['locale'],
              'native': native, 'japanese': None, 'retry_skipped_reason': None}
    if not decision['should_retry_japanese']:
        return result
    if native.get('target_active') is not False:
        result['retry_skipped_reason'] = 'native_target_not_confirmed_stopped'
        return result
    try:
        japanese = probe_game(exe, 'japanese', runtime=runtime, timeout=timeout,
                              readiness=readiness, terminate_owned=terminate_owned)
    except (OSError, ValueError) as exc:
        result['retry_skipped_reason'] = 'japanese_probe_unavailable'
        result['retry_error'] = str(exc)[:300]
        return result
    result['japanese'] = japanese
    final = compare_boot_results(native, japanese)
    result['status'], result['locale'] = final['status'], final['locale']
    return result
