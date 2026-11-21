"""Read-only Windows DXGI adapter capacity; no inference or driver mutation."""
import ctypes
import os
from ctypes import wintypes


class GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]


class LUID(ctypes.Structure):
    _fields_ = [('LowPart', ctypes.c_uint32), ('HighPart', ctypes.c_int32)]


class DESC1(ctypes.Structure):
    _fields_ = [('Description', wintypes.WCHAR * 128), ('VendorId', ctypes.c_uint32),
                ('DeviceId', ctypes.c_uint32), ('SubSysId', ctypes.c_uint32),
                ('Revision', ctypes.c_uint32), ('DedicatedVideoMemory', ctypes.c_size_t),
                ('DedicatedSystemMemory', ctypes.c_size_t), ('SharedSystemMemory', ctypes.c_size_t),
                ('AdapterLuid', LUID), ('Flags', ctypes.c_uint32)]


def _method(obj, index, result, *arguments):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])


def dxgi_adapters():
    """Hardware adapter inventory. LUID is not a Vulkan index or CUDA UUID."""
    if os.name != 'nt': return []
    dxgi = ctypes.WinDLL('dxgi')
    create = dxgi.CreateDXGIFactory1
    create.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_long
    iid = GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(0xa8,0x29,0x25,0x3c,0x83,0xd1,0xb3,0x87))
    factory = ctypes.c_void_p()
    if create(ctypes.byref(iid), ctypes.byref(factory)) < 0:
        raise OSError('DXGI factory creation failed')
    adapters = []
    try:
        enum = _method(factory, 12, ctypes.c_long, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))
        for index in range(256):
            adapter = ctypes.c_void_p()
            status = enum(factory, index, ctypes.byref(adapter))
            if status & 0xffffffff == 0x887a0002: break
            if status < 0: raise OSError('DXGI adapter enumeration failed')
            try:
                desc = DESC1()
                get_desc = _method(adapter, 10, ctypes.c_long, ctypes.POINTER(DESC1))
                if get_desc(adapter, ctypes.byref(desc)) < 0: raise OSError('DXGI adapter description failed')
                if desc.Flags & 2: continue  # DXGI_ADAPTER_FLAG_SOFTWARE
                luid = '%08x%08x' % (desc.AdapterLuid.HighPart & 0xffffffff, desc.AdapterLuid.LowPart)
                adapters.append(dict(id='DXGI-'+luid, name=desc.Description,
                    vendor_id=desc.VendorId, device_id=desc.DeviceId, luid=luid,
                    vendor='amd' if desc.VendorId == 0x1002 else 'nvidia' if desc.VendorId == 0x10de else 'other',
                    total=desc.DedicatedVideoMemory / (1024 * 1024), used=None,
                    utilization=None, driver=None, selectable=False, active=None))
            finally:
                _method(adapter, 2, ctypes.c_ulong)(adapter)
    finally:
        _method(factory, 2, ctypes.c_ulong)(factory)
    return adapters


def performance_values(adapter_rows, engine_rows, process_rows, owners):
    """Map Windows GPU performance counter instances to the DXGI LUID.

    Utilization is the maximum of per-engine totals, matching the convention
    used to avoid adding concurrently busy engines into a percentage above 100.
    Process memory is only evidence when the PID belongs to a loaded runtime.
    """
    import re
    luid_pattern = re.compile(r'luid_0x([0-9a-f]{8})_0x([0-9a-f]{8})', re.I)
    pid_pattern = re.compile(r'(?:^|_)pid_(\d+)(?:_|$)', re.I)
    engine_pattern = re.compile(r'_phys_(\d+)_eng_(\d+)(?:_|$)', re.I)
    def luid(name):
        m=luid_pattern.search(name or '')
        return (m.group(1)+m.group(2)).lower() if m else None
    result={}
    for row in adapter_rows:
        key=luid(row.get('Name'))
        if key and isinstance(row.get('DedicatedUsage'),(int,float)):
            entry=result.setdefault(key,{})
            entry['used']=entry.get('used',0)+row['DedicatedUsage']/1024**2
    engines={}
    for row in engine_rows:
        name=row.get('Name') or '';key=luid(name);engine=engine_pattern.search(name);value=row.get('UtilizationPercentage')
        if key and engine and isinstance(value,(int,float)):
            ident=(key,engine.group(1),engine.group(2));engines[ident]=engines.get(ident,0)+value
    for (key,_,_),value in engines.items():
        entry=result.setdefault(key,{})
        entry['utilization']=round(min(100,max(entry.get('utilization',0),value)),1)
    for row in process_rows:
        name=row.get('Name') or '';key=luid(name);pid=pid_pattern.search(name)
        if key and pid and int(pid.group(1)) in owners and (row.get('DedicatedUsage') or 0)>0:
            result.setdefault(key,{})['active']=True
    return result


def merge_drivers(devices, rows):
    """Merge only unambiguous controller matches, without using AdapterRAM."""
    import re
    for gpu in rows:
        name=gpu.get('Name');pnp=gpu.get('PNPDeviceID') or ''
        matches=[d for d in devices if d.get('name')==name]
        if len(matches)!=1:
            match=re.search(r'VEN_([0-9a-f]{4}).*DEV_([0-9a-f]{4})',pnp,re.I)
            matches=[d for d in devices if match and d.get('vendor_id')==int(match.group(1),16)
                     and d.get('device_id')==int(match.group(2),16)]
        if len(matches)==1:
            if not matches[0].get('driver'): matches[0]['driver']=gpu.get('DriverVersion')
        elif len(matches)>1:
            # The device instance cannot be resolved from a model name alone.
            # A version is safe to share only if every controller for the
            # identical PCI identity and name reports the same nonempty value.
            identities={(d.get('vendor_id'),d.get('device_id')) for d in matches}
            if len(identities)==1 and None not in next(iter(identities)):
                identity=next(iter(identities))
                group=[]
                for row in rows:
                    found=re.search(r'VEN_([0-9a-f]{4}).*DEV_([0-9a-f]{4})',row.get('PNPDeviceID') or '',re.I)
                    if row.get('Name')==name and found and (int(found.group(1),16),int(found.group(2),16))==identity:
                        group.append(row.get('DriverVersion'))
                if len(group)==len(matches) and all(group) and len(set(group))==1:
                    for device in matches:
                        if not device.get('driver'): device['driver']=group[0]
        elif not matches and name:
            devices.append(dict(id=pnp or name,name=name,total=None,used=None,utilization=None,
                                driver=gpu.get('DriverVersion'),selectable=False,active=None))
    return devices
