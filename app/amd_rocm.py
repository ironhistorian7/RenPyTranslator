"""Isolated, read-only HIP inventory and conservative DXGI identity matching."""
import json
import os
from pathlib import Path
import subprocess
import sys

OFFICIAL_VARIANT='rocm_v7_1'
_CLEARED=('CUDA_VISIBLE_DEVICES','HIP_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES',
          'GPU_DEVICE_ORDINAL','GGML_VK_VISIBLE_DEVICES','HSA_OVERRIDE_GFX_VERSION',
          'OLLAMA_LLM_LIBRARY','OLLAMA_LIBRARY_PATH')


def _normal_name(value):
    return ' '.join(str(value or '').casefold().split())


def _pci(value):
    import re
    match=re.fullmatch(r'(?:(\w{4}):)?([0-9a-f]{2}):([0-9a-f]{2})\.([0-7])',str(value or '').lower())
    return (match.group(1) or '0000')+':'+':'.join(match.group(2,3))+'.'+match.group(4) if match else None


def _close_memory(a,b):
    try:
        a=float(a);b=float(b)
        return a>0 and b>0 and abs(a-b)<=max(128.0, max(a,b)*0.01)
    except (ValueError,TypeError):return False


def hip_inventory(dll_path):
    """Called only in a helper process; no GPU work or model execution."""
    if os.name!='nt':return []
    import ctypes as C
    path=Path(dll_path).resolve(strict=True)
    if path.name.lower()!='amdhip64_7.dll':raise ValueError('Invalid HIP runtime')
    with os.add_dll_directory(str(path.parent)):
        lib=C.WinDLL(str(path))
        count=lib.hipGetDeviceCount;count.argtypes=[C.POINTER(C.c_int)];count.restype=C.c_int
        name=lib.hipDeviceGetName;name.argtypes=[C.c_char_p,C.c_int,C.c_int];name.restype=C.c_int
        total=lib.hipDeviceTotalMem;total.argtypes=[C.POINTER(C.c_size_t),C.c_int];total.restype=C.c_int
        pci=lib.hipDeviceGetPCIBusId;pci.argtypes=[C.c_char_p,C.c_int,C.c_int];pci.restype=C.c_int
        n=C.c_int()
        if count(C.byref(n)) or not 0<=n.value<=256:raise RuntimeError('HIP device enumeration failed')
        result=[]
        for index in range(n.value):
            label=C.create_string_buffer(256); bus=C.create_string_buffer(64); memory=C.c_size_t()
            if name(label,len(label),index) or total(C.byref(memory),index) or pci(bus,len(bus),index):continue
            result.append(dict(index=index,name=label.value.decode('utf-8','replace'),
                               total_mib=memory.value/1048576,pci_bus_id=bus.value.decode('ascii','replace')))
        return result


def _match(device, entries, peers):
    target_pci=_pci(device.get('pci_bus_id') or device.get('pci_id'))
    if target_pci:
        matches=[x for x in entries if _pci(x.get('pci_bus_id'))==target_pci]
        return matches[0] if len(matches)==1 else None
    name=_normal_name(device.get('name'));memory=device.get('total_mib',device.get('total'))
    if not name or not _close_memory(memory,memory):return None
    same=lambda x: _normal_name(x.get('name'))==name and _close_memory(memory,x.get('total_mib',x.get('total')))
    matches=[x for x in entries if same(x)]
    peers=[x for x in peers if same(x)]
    if len(matches)!=1 or len(peers)!=1:return None
    # Prove that the unique DXGI peer is the requested adapter, not a stale plan.
    luid=device.get('luid')
    if not luid or str(peers[0].get('luid','')).lower()!=str(luid).lower():return None
    return matches[0]


def resolve_rocm(executable, device, env, *, library_directory=None):
    """Find a uniquely matching adapter in the installed official HIP pack."""
    from gpu_inventory import dxgi_adapters
    from native_process import run
    folder=Path(executable).resolve().parent/'lib'/'ollama'/OFFICIAL_VARIANT
    hip=folder/'amdhip64_7.dll'
    if not hip.is_file() or not (folder/'ggml-hip.dll').is_file():return None
    child_env=dict(env)
    for key in list(child_env):
        if key.upper() in _CLEARED:child_env.pop(key,None)
    try:peers=dxgi_adapters()
    except Exception:peers=[]
    if getattr(sys,'frozen',False):
        args=[sys.executable,'--rocm-inventory',str(hip)]
    else:
        code=('import json,sys;sys.path.insert(0,sys.argv[1]);from amd_rocm import hip_inventory;'
              'print(json.dumps(hip_inventory(sys.argv[2])))')
        args=[sys.executable,'-c',code,str(Path(__file__).resolve().parent),str(hip)]
    try:
        proc=run(args,env=child_env,library_directory=library_directory,capture_output=True,
                 encoding='utf-8',errors='replace',timeout=30,
                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
        entries=json.loads(proc.stdout)
        if not isinstance(entries,list) or not all(isinstance(entry,dict) for entry in entries):return None
        match=_match(device,entries,peers)
        if match and type(match.get('index')) is int and match['index']>=0:
            return {key:match.get(key) for key in ('index','name','pci_bus_id')} | {'variant':OFFICIAL_VARIANT}
    except (OSError,ValueError,TypeError,subprocess.SubprocessError):pass
    return None
