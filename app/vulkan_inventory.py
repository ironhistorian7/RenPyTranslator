"""Read-only Vulkan physical device identities; indices require runner verification."""
import ctypes as C
import os

class _ApplicationInfo(C.Structure):
    _fields_=[('sType',C.c_uint32),('pNext',C.c_void_p),('pApplicationName',C.c_char_p),
              ('applicationVersion',C.c_uint32),('pEngineName',C.c_char_p),
              ('engineVersion',C.c_uint32),('apiVersion',C.c_uint32)]

class _InstanceCreateInfo(C.Structure):
    _fields_=[('sType',C.c_uint32),('pNext',C.c_void_p),('flags',C.c_uint32),
              ('pApplicationInfo',C.POINTER(_ApplicationInfo)),('enabledLayerCount',C.c_uint32),
              ('ppEnabledLayerNames',C.c_void_p),('enabledExtensionCount',C.c_uint32),
              ('ppEnabledExtensionNames',C.c_void_p)]

class _IDProperties(C.Structure):
    _fields_=[('sType',C.c_uint32),('pNext',C.c_void_p),('deviceUUID',C.c_ubyte*16),
              ('driverUUID',C.c_ubyte*16),('deviceLUID',C.c_ubyte*8),
              ('deviceNodeMask',C.c_uint32),('deviceLUIDValid',C.c_uint32)]

# Vulkan writes VkPhysicalDeviceProperties here.  Allocate extra trailing space
# to avoid relying on the ABI layout of the large nested limits structure.
class _Properties2(C.Structure):
    _fields_=[('sType',C.c_uint32),('pNext',C.c_void_p),('properties',C.c_uint64*256)]

class _PropertiesPrefix(C.Structure):
    _fields_=[('apiVersion',C.c_uint32),('driverVersion',C.c_uint32),
              ('vendorID',C.c_uint32),('deviceID',C.c_uint32),('deviceType',C.c_uint32),
              ('deviceName',C.c_char*256)]

def vulkan_devices():
    """Return loader enumeration indices and stable IDs, without creating a device.

    The index is not assumed to match the GGML runner index; callers must verify
    that mapping from the actual runner's reported device identity.
    """
    if os.name!='nt': return []
    try: vk=C.WinDLL('vulkan-1.dll')
    except OSError: return []
    create=vk.vkCreateInstance
    create.argtypes=[C.POINTER(_InstanceCreateInfo),C.c_void_p,C.POINTER(C.c_void_p)]
    create.restype=C.c_int32
    app=_ApplicationInfo(0,None,b'RenPyTranslator',0,b'',0,(1<<22)|(1<<12))
    info=_InstanceCreateInfo(1,None,0,C.pointer(app),0,None,0,None)
    instance=C.c_void_p()
    status=create(C.byref(info),None,C.byref(instance))
    if status: raise OSError('Vulkan instance creation failed: '+str(status))
    try:
        enum=vk.vkEnumeratePhysicalDevices
        enum.argtypes=[C.c_void_p,C.POINTER(C.c_uint32),C.POINTER(C.c_void_p)]
        enum.restype=C.c_int32
        count=C.c_uint32()
        status=enum(instance,C.byref(count),None)
        if status: raise OSError('Vulkan device enumeration failed: '+str(status))
        if not count.value: return []
        devices=(C.c_void_p*count.value)()
        status=enum(instance,C.byref(count),devices)
        if status: raise OSError('Vulkan device enumeration incomplete: '+str(status))
        get=vk.vkGetPhysicalDeviceProperties2
        get.argtypes=[C.c_void_p,C.POINTER(_Properties2)]
        get.restype=None
        found=[]
        for index,device in enumerate(devices[:count.value]):
            ids=_IDProperties(); ids.sType=1000071004
            props=_Properties2(); props.sType=1000059001; props.pNext=C.addressof(ids)
            get(device,C.byref(props))
            prefix=C.cast(C.addressof(props.properties),C.POINTER(_PropertiesPrefix)).contents
            valid=bool(ids.deviceLUIDValid)
            luid=('%016x'%int.from_bytes(bytes(ids.deviceLUID),'little')) if valid else None
            found.append(dict(index=index,name=bytes(prefix.deviceName).decode('utf-8','replace'),
                              vendor_id=prefix.vendorID,device_id=prefix.deviceID,
                              luid=luid,luid_valid=valid,backend='vulkan',
                              device_uuid=bytes(ids.deviceUUID).hex(),device_type=prefix.deviceType))
        return found
    finally:
        destroy=vk.vkDestroyInstance
        destroy.argtypes=[C.c_void_p,C.c_void_p]; destroy.restype=None
        destroy(instance,None)


def resolve_selector(device,env,library_directory=None):
    """Resolve an adapter in a fresh process with the exact child environment.

    Vulkan layer loading happens once per process; the parent's inventory cannot
    establish enumeration under a different layer policy. Refuse missing or
    ambiguous stable identities instead of guessing from name or old index.
    """
    import json
    import subprocess
    import sys
    from pathlib import Path
    from native_process import run
    luid=device.get('luid')
    vendor=device.get('vendor_id')
    chip=device.get('device_id')
    if not luid or vendor is None or chip is None:
        raise ValueError('Vulkan device requires LUID, vendor_id and device_id')
    if getattr(sys,'frozen',False):
        args=[sys.executable,'--vulkan-inventory']
    else:
        code=('import json,sys;sys.path.insert(0,sys.argv[1]);'
              'from vulkan_inventory import vulkan_devices;'
              'print(json.dumps(vulkan_devices(),ensure_ascii=True))')
        args=[sys.executable,'-c',code,str(Path(__file__).resolve().parent)]
    result=run(args,env=dict(env),library_directory=library_directory,
               capture_output=True,encoding='utf-8',errors='replace',timeout=30,
               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
    try: entries=json.loads(result.stdout)
    except (ValueError,TypeError) as exc: raise RuntimeError('Vulkan inventory helper returned invalid output') from exc
    if not isinstance(entries,list): raise RuntimeError('Vulkan inventory helper returned invalid data')
    matches=[item for item in entries if item.get('luid_valid') and
             str(item.get('luid','')).lower()==str(luid).lower() and
             item.get('vendor_id')==vendor and item.get('device_id')==chip]
    if len(matches)!=1: raise RuntimeError('Vulkan adapter identity was not uniquely resolved in child environment')
    return matches[0]
