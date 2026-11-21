import unittest
from unittest.mock import patch
from gpu_dispatcher import RuntimeDispatch

class TransportError(Exception): _rpt_transport=True
class Pool:
    def __init__(self):
        self.current={x:dict(device_id=x,worker_slots=n,endpoint=x,parallel=n) for x,n in [('a',2),('b',1)]};self.stops=[];self.starts=[]
    @property
    def workers(self): return list(self.current.values())
    def stop(self,i): self.stops.append(i);self.current.pop(i)
    def start(self,i):
        self.starts.append(i);result=dict(device_id=i,worker_slots=2 if i=='a' else 1,endpoint=i+str(len(self.starts)));self.current[i]=result;return result

class Tests(unittest.TestCase):
    def setUp(self):
        self.pool=Pool();self.d=RuntimeDispatch(self.pool,'unused',{'model':'fixture'},lambda:0);self.clock=patch('gpu_dispatcher.time.monotonic',return_value=0);self.clock.start()
    def tearDown(self):self.clock.stop()
    def test_round_robin_reverse_completion(self):
        a=self.d.acquire();b=self.d.acquire();a2=self.d.acquire();self.assertEqual([a['_dispatch_device_id'],b['_dispatch_device_id'],a2['_dispatch_device_id']],['a','b','a']);self.assertIsNone(self.d.acquire())
        self.d.release(a2);self.d.release(b);self.d.release(a);self.assertEqual(self.d.capacity,3)
    def test_preferred_model_then_immediate_healthy_fallback(self):
        self.d.states['a']['active']['model']='alpha'
        self.d.states['b']['active']['model']='beta'
        preferred=self.d.acquire(preferred_model='beta')
        self.assertEqual(preferred['device_id'],'b')
        fallback=self.d.acquire(preferred_model='beta')
        self.assertEqual(fallback['device_id'],'a')
        self.d.release(preferred);self.d.release(fallback)

    def test_failure_isolated_and_waits_for_old_leases(self):
        a=self.d.acquire();b=self.d.acquire();a2=self.d.acquire();e=TransportError()
        self.d.failed(a,e);self.d.failed(a2,e);self.assertEqual(self.pool.stops,['a']);self.assertEqual(self.d.capacity,1);self.d.release(a)
        with patch('gpu_dispatcher.time.monotonic',return_value=11):self.d.tick()
        self.assertEqual(self.pool.starts,[]);self.d.release(a2);self.d.release(b)
        with patch('gpu_dispatcher.time.monotonic',return_value=11):self.d.tick()
        self.assertEqual(self.pool.starts,['a']);self.assertEqual(self.d.capacity,3)
    def test_delays_and_exhaustion(self):
        for delay in (10,30,180):
            active=self.d.acquire();self.assertEqual(active['_dispatch_device_id'],'a');self.d.failed(active,TransportError());self.d.release(active)
            self.assertEqual(self.d.states['a']['deadline'],delay)
            with patch('gpu_dispatcher.time.monotonic',return_value=delay):self.d.tick()
            b=self.d.acquire();self.assertEqual(b['_dispatch_device_id'],'b');self.d.release(b)
        a=self.d.acquire();self.d.failed(a,TransportError());self.d.release(a);self.assertTrue(self.d.live);self.assertTrue(self.d.states['a']['exhausted'])
        b=self.d.acquire();self.d.states['b']['streak']=3
        with self.assertRaises(TransportError):self.d.failed(b,TransportError())
        self.d.release(b);self.assertFalse(self.d.live)
    def test_committed_only_resets_own_device(self):
        a=self.d.acquire();b=self.d.acquire();self.d.states['a']['streak']=2;self.d.states['b']['streak']=2
        self.d.committed(a,[]);self.assertEqual(self.d.states['a']['streak'],2)
        self.d.committed(a,['item']);self.assertEqual(self.d.states['a']['streak'],0);self.assertEqual(self.d.states['b']['streak'],2)
        self.d.release(a);self.d.release(b)
    def test_event_reserved_identity_uses_dispatcher_generation(self):
        class Evidence:
            def __init__(self): self.events=[]
            def event(self,name,**details): self.events.append((name,details))
        evidence=Evidence();self.d.evidence=evidence
        state=self.d.states['a'];state['generation']=7;self.d._ready('a',state)
        state['active']['_recovery_event']('request_started',generation=999,device_id='wrong',run_id='wrong',request_id='r')
        name,details=evidence.events[-1]
        self.assertEqual(name,'request_started');self.assertEqual(details['generation'],7)
        self.assertEqual(details['device_id'],'a');self.assertEqual(details['run_id'],self.d.run_id)
        self.assertEqual(details['request_id'],'r')
    def test_healthy_device_never_pauses_ui(self):
        a=self.d.acquire()
        with patch.object(self.d.tracker,'pause') as pause, patch('gpu_dispatcher.emit') as emit:
            self.d.failed(a,TransportError());self.d.release(a)
            pause.assert_not_called();emit.assert_not_called()
    def test_all_waiting_then_first_recovery_restores_ui(self):
        a=self.d.acquire();b=self.d.acquire()
        with patch.object(self.d.tracker,'pause') as pause, patch.object(self.d.tracker,'resume') as resume, patch('gpu_dispatcher.emit') as emit:
            self.d.failed(a,TransportError());self.d.release(a)
            self.d.failed(b,TransportError());self.d.release(b)
            pause.assert_called_once();self.assertEqual(emit.call_args.kwargs['state'],'waiting')
            self.assertEqual(emit.call_args.kwargs['delay_seconds'],10)
            with patch('gpu_dispatcher.time.monotonic',return_value=10):self.d.tick()
            resume.assert_called_once();self.assertEqual(emit.call_args.kwargs['state'],'resuming')
            self.d.tick();resume.assert_called_once()
    def test_nonrecoverable_propagates(self):
        a=self.d.acquire()
        with self.assertRaises(ValueError):self.d.failed(a,ValueError('bad'))
        self.assertEqual(self.pool.stops,[]);self.d.release(a)
if __name__=='__main__':unittest.main()
