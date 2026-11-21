import unittest
from gpu_inventory import performance_values, merge_drivers

class GpuTelemetryTests(unittest.TestCase):
    def test_luid_per_engine_and_owner_proof(self):
        adapter=[{'Name':'luid_0x00000001_0x00000002_phys_0','DedicatedUsage':2*1024**2}]
        engine=[{'Name':f'pid_{pid}_luid_0x00000001_0x00000002_phys_0_eng_{eng}_engtype_Compute','UtilizationPercentage':value} for pid,eng,value in [(7,0,30),(8,0,20),(7,1,60)]]
        process=[{'Name':'pid_7_luid_0x00000001_0x00000002_phys_0','DedicatedUsage':1024}]
        result=performance_values(adapter,engine,process,{7})['0000000100000002']
        self.assertEqual(result,{'used':2,'utilization':60,'active':True})
        self.assertNotIn('active',performance_values(adapter,engine,process,{8})['0000000100000002'])
        process[0]['DedicatedUsage']=0
        self.assertNotIn('active',performance_values(adapter,engine,process,{7})['0000000100000002'])
    def test_driver_merge_is_not_ram_capacity(self):
        devices=[dict(name='A',vendor_id=0x1002,device_id=0x1234,total=16384,driver=None)]
        merge_drivers(devices,[{'Name':'A','PNPDeviceID':'PCI\\VEN_1002&DEV_1234','DriverVersion':'1.2','AdapterRAM':1}])
        self.assertEqual(len(devices),1);self.assertEqual(devices[0]['driver'],'1.2');self.assertEqual(devices[0]['total'],16384)

    def test_duplicate_controllers_require_complete_consistent_group(self):
        def devices():
            return [dict(name='AMD Same',vendor_id=0x1002,device_id=0x1234,luid=str(i),driver=None) for i in range(2)]
        def row(version):
            return {'Name':'AMD Same','PNPDeviceID':'PCI\\VEN_1002&DEV_1234','DriverVersion':version}
        for rows,expected in [([row('1.2'),row('1.2')],['1.2','1.2']),
                              ([row('1.2'),row('2.0')],[None,None]),
                              ([row('1.2'),row(None)],[None,None]),
                              ([row('1.2')],[None,None])]:
            cards=devices();merge_drivers(cards,rows)
            self.assertEqual([d['driver'] for d in cards],expected)
            self.assertEqual([d['luid'] for d in cards],['0','1'])

if __name__=='__main__':unittest.main()
