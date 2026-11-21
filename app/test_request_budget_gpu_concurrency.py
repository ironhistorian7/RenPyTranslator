import tempfile
import threading
import unittest
from collections import Counter as Counts
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import request_budget


class TokenizerConcurrencyTests(unittest.TestCase):
    def test_distinct_endpoints_warm_up_concurrently_and_same_endpoint_once(self):
        with tempfile.TemporaryDirectory() as directory:
            paths=[Path(directory)/f'{i}.log' for i in range(2)]
            for i,path in enumerate(paths): path.write_text(f'--port {14000+i}',encoding='utf8')
            configs=[dict(endpoint=f'http://127.0.0.1:{13000+i}',model='fake',num_ctx=2048,_runtime_log=str(path)) for i,path in enumerate(paths)]
            both=threading.Barrier(2,timeout=5); guard=threading.Lock(); calls=Counts()
            def post(endpoint,route,body):
                with guard: calls[(endpoint,route)]+=1
                if route=='/api/generate': both.wait(); return {}
                return {'tokens':[1,2]}
            with request_budget._LOCK:
                request_budget._COUNTERS.clear();request_budget._COUNTER_LOCKS.clear()
            self.addCleanup(lambda:[request_budget.forget(c) for c in configs])
            with patch('request_budget.post',side_effect=post),ThreadPoolExecutor(max_workers=8) as executor:
                futures=[executor.submit(request_budget.counter,configs[i%2]) for i in range(8)]
                counters=[f.result(timeout=8) for f in futures]
            self.assertTrue(all(counters[i] is counters[i%2] for i in range(8)))
            self.assertEqual(calls[(configs[0]['endpoint'],'/api/generate')],1)
            self.assertEqual(calls[(configs[1]['endpoint'],'/api/generate')],1)
            self.assertTrue(all(c.method=='owned_model_tokenizer' for c in counters))

if __name__=='__main__':unittest.main()
