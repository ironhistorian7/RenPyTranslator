"""Independent, tool-owned GPU servers for the parent translation scheduler."""
from contextlib import contextmanager
from threading import RLock
from hy_backend import has_vulkan_route, runtime

class RuntimePool:
    """Own one server per GPU. The caller quiesces leases before restart/stop."""
    def __init__(self,cfg,devices):
        self.cfg=dict(cfg)
        self.devices={}
        self._contexts={}
        self._active={}
        self._generations={}
        self._vulkan_fallbacks={}
        self._lock=RLock()
        for raw in devices:
            device=dict(raw)
            device['config']=dict(device.get('config',{}))
            device['overrides']=dict(device.get('overrides',{}))
            identity=device.get('id')
            backend=device.get('backend')
            selector=device.get('selector')
            if not identity or identity in self.devices: raise ValueError('GPU IDs must be unique')
            if backend not in ('cuda','vulkan','rocm') or selector is None or str(selector)=='':
                raise ValueError('GPU requires an explicit backend and selector')
            if backend in ('vulkan','rocm') and not str(selector).isdigit():
                raise ValueError('AMD selector must be a verified runner index')
            if backend=='cuda' and device.get('vendor')!='nvidia':
                raise ValueError('CUDA worker must identify an NVIDIA device')
            if backend in ('vulkan','rocm') and device.get('vendor')!='amd':
                raise ValueError('Vulkan/ROCm worker must identify an AMD device')
            self.devices[identity]=device
            self._generations[identity]=-1
        if not self.devices: raise ValueError('GPU runtime pool requires at least one device')

    @property
    def workers(self):
        with self._lock: return list(self._active.values())

    def start(self,device_id):
        with self._lock:
            if device_id in self._active: raise ValueError('GPU server already active')
            device=self.devices[device_id]
            cfg=dict(self.cfg)
            cfg.update(device.get('config',{}))
            # Each child starts with a clean launch state, including on restart.
            # A stale parent endpoint/PID is never evidence for this generation.
            for key in ('endpoint','_runtime_pid','_runtime_log','_runtime_launch',
                        '_runtime_exit','_runtime_env','_runtime_model_metadata',
                        '_model_registration_compatibility'):
                cfg.pop(key,None)
            if cfg.get('_hardware_pending_model'):
                raise ValueError('Prepare the shared local model before starting the GPU pool')
            cfg['parallel']=int(device.get('parallel',cfg.get('parallel',2)))
            if cfg['parallel']<1: raise ValueError('GPU parallel must be positive')
            # The parent validated this plan. Mark it frozen so runtime.configure
            # cannot apply saved global preferences over the per-device profile.
            policy=dict(device.get('config',{}).get('_hardware_policy') or {})
            if self.cfg.get('_hardware_policy'):
                policy.setdefault('parent_policy',dict(self.cfg['_hardware_policy']))
            policy.update(status=policy.get('status','planned'),vendor=device['vendor'],
                          capacity_device=device['id'],backend=device['backend'],
                          num_ctx=cfg.get('num_ctx'),parallel=cfg['parallel'],
                          kv_cache_type=cfg.get('kv_cache_type','q8_0'),
                          effective_settings={k:cfg[k] for k in ('num_ctx','parallel','kv_cache_type','context_txt_enabled') if k in cfg},
                          device_overrides=dict(device['overrides']),
                          effective_model=cfg.get('model'),source='mixed_gpu_plan')
            cfg['_hardware_policy']=policy
            cfg['_runtime_device']={k:v for k,v in device.items() if k not in ('config','parallel')}
            cfg['_runtime_pool_managed']=True
            if device_id in self._vulkan_fallbacks:
                cfg.update(_amd_force_vulkan=True, _amd_fallback_reason=self._vulkan_fallbacks[device_id])
            self._generations[device_id]+=1
            cfg['_runtime_generation']=self._generations[device_id]
            ctx=runtime(cfg)
            active=ctx.__enter__()
            backend=active.get('_runtime_device',{}).get('backend',device['backend'])
            reason=active.get('_amd_fallback_reason')
            if reason:
                self._vulkan_fallbacks[device_id]=reason
            active.update(device_id=device_id,device_vendor=device['vendor'],
                          device_backend=backend,worker_slots=active.get('parallel',cfg['parallel']))
            self._contexts[device_id]=ctx
            self._active[device_id]=active
            return active

    def fallback_after_failure(self,device_id,error):
        with self._lock:
            if (not has_vulkan_route(self.devices[device_id])
                    or self._active.get(device_id,{}).get('device_backend')!='rocm'):
                return
            self._vulkan_fallbacks[device_id]=repr(error)
            print(f"GPU warning: {self.devices[device_id]['name']} ROCm 연결 실패 · 다음 시도는 Vulkan", flush=True)

    def stop(self,device_id):
        """Call only after the scheduler has quiesced this device's requests."""
        with self._lock:
            ctx=self._contexts.pop(device_id,None)
            self._active.pop(device_id,None)
            if ctx: ctx.__exit__(None,None,None)

    def restart(self,device_id):
        """Restart only this device; caller must first quiesce its requests."""
        with self._lock:
            self.stop(device_id)
            return self.start(device_id)

    def close(self):
        with self._lock:
            failures=[]
            for device_id in list(self._contexts):
                try: self.stop(device_id)
                except Exception as exc: failures.append(exc)
            if failures: raise failures[0]

@contextmanager
def runtime_pool(cfg,devices):
    pool=RuntimePool(cfg,devices)
    try:
        for device_id in pool.devices: pool.start(device_id)
        yield pool
    finally: pool.close()
