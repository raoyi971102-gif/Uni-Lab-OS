"""软件契约测试：模拟原厂接口，不代表任何型号的实机验证。"""
import ctypes as C
import importlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from unilabos.devices.ivi_family import driver as d
from unilabos.devices.ivi_family.com_scope import IviComScope


class FakeDLL:
    def __init__(self, model='UXR0134B', fail=None, oversized=False):
        self.calls=[]; self.model=model; self.fail=fail; self.oversized=oversized
        signatures={
            'InitWithOptions':[d.T,d.B,d.B,d.T,d.P(d.S)], 'close':[d.S],
            'error_message':[d.S,d.I,d.P(C.c_char)],
            'GetAttributeViString':[d.S,d.T,d.I,d.I,d.P(C.c_char)],
            'ConfigureChannel':[d.S,d.T,d.D,d.D,d.I,d.D,d.B],
            'ConfigureAcquisitionRecord':[d.S,d.D,d.I,d.D],
            'ReadWaveform':[d.S,d.T,d.I,d.I,d.P(d.D),d.P(d.I),d.P(d.D),d.P(d.D)],
            'Abort':[d.S], 'ConfigureVoltageLevel':[d.S,d.T,d.D],
            'ConfigureCurrentLimit':[d.S,d.T,d.I,d.D],
            'ConfigureOutputEnabled':[d.S,d.T,d.B],
            'Measure':[d.S,d.T,d.I,d.P(d.D)],
            'ConfigureOutputMode':[d.S,d.I],
            'ConfigureStandardWaveform':[d.S,d.T,d.I,d.D,d.D,d.D,d.D],
            'InitiateGeneration':[d.S], 'AbortGeneration':[d.S]}
        # 真正的 ctypes 函数指针检查参数 ABI，而不只是 Python mock 接收任意参数。
        for name,args in signatures.items():
            fn=C.WINFUNCTYPE(d.I,*args)(self.handler(name))
            setattr(self,'Test_'+name,fn)

    def handler(self,name):
        def call(*args):
            self.calls.append((name,args))
            if name==self.fail:return -123
            if name=='InitWithOptions':args[-1][0]=17
            elif name=='GetAttributeViString':C.memmove(args[-1],self.model.encode()+b'\0',len(self.model)+1)
            elif name=='error_message':C.memmove(args[-1],b'failure\0',8)
            elif name=='ReadWaveform':
                args[4][0]=1.25; args[5][0]=args[2]+1 if self.oversized else 1
                args[6][0]=-0.5;args[7][0]=0.001
            elif name=='Measure':args[-1][0]=12.5
            return 0
        return call


class TestIviC(unittest.TestCase):
    def open(self,lib,cls=d.IviCScope,model='UXR0134B',family='infiniium'):
        with patch.object(d.IviCDevice,'_load_library',return_value=lib):
            return cls('TCPIP::example::INSTR',model,family,'driver.dll','Test')

    def test_scope_abi_and_values(self):
        lib=FakeDLL();obj=self.open(lib)
        init=lib.calls[0][1];self.assertEqual(init[1:3],(1,0));self.assertIn(b'Simulate=0',init[3])
        obj.configure_channel('Channel1',2.0);obj.configure_record(.01,100)
        self.assertEqual(obj.read_waveform('Channel1',100),{'volts':[1.25],'initial_seconds':-.5,'interval_seconds':.001})
        obj.abort();obj.close();obj.close()
        self.assertEqual(sum(x[0]=='close' for x in lib.calls),1)
        with self.assertRaises(RuntimeError):obj.abort()

    def test_mismatch_closes(self):
        lib=FakeDLL('WRONG')
        with self.assertRaisesRegex(ValueError,'不一致'):self.open(lib)
        self.assertEqual(lib.calls[-1][0],'close')

    def test_init_failure(self):
        with self.assertRaisesRegex(RuntimeError,'-123'):self.open(FakeDLL(fail='InitWithOptions'))

    def test_negative_call_status(self):
        obj=self.open(FakeDLL(fail='ReadWaveform'))
        try:
            with self.assertRaisesRegex(RuntimeError,'failure'):obj.read_waveform('1')
        finally:obj.close()

    def test_invalid_point_count(self):
        obj=self.open(FakeDLL(oversized=True))
        try:
            with self.assertRaisesRegex(RuntimeError,'缓冲区'):obj.read_waveform('1',2)
            for value in [-1,0,10000001,True,2.5]:
                with self.subTest(value=value),self.assertRaises(ValueError):obj.read_waveform('1',value)
            with self.assertRaises(ValueError):obj.configure_channel('1',float('nan'))
        finally:obj.close()

    def test_unknown_model_no_library_load(self):
        with patch.object(d.IviCDevice,'_load_library') as load:
            with self.assertRaises(ValueError):d.IviCScope('x','UXR9999','infiniium','x','X')
            load.assert_not_called()

    def test_missing_symbol_before_connect(self):
        lib=FakeDLL();del lib.Test_ReadWaveform
        with self.assertRaisesRegex(RuntimeError,'缺少'):self.open(lib)
        self.assertEqual(lib.calls,[])

    def test_power_configuration_order_and_measurement(self):
        lib=FakeDLL('N5741A');obj=self.open(lib,d.IviCPowerSupply,'N5741A','n57xx')
        obj.configure_output('1',12,0.5)
        self.assertEqual([c[0] for c in lib.calls[-3:]],['ConfigureOutputEnabled','ConfigureCurrentLimit','ConfigureVoltageLevel'])
        self.assertEqual(lib.calls[-3][1][-1],0)
        self.assertEqual(lib.calls[-2][1][2:],(1,.5))
        self.assertEqual(obj.measure('1'),12.5)
        self.assertEqual(lib.calls[-1][1][2],1)
        obj.set_output_enabled('1',False);obj.close()

    def test_failed_power_config_never_enables(self):
        lib=FakeDLL('N5741A',fail='ConfigureCurrentLimit');obj=self.open(lib,d.IviCPowerSupply,'N5741A','n57xx')
        with self.assertRaises(RuntimeError):obj.configure_output('1',12,.5)
        self.assertFalse(any(n=='ConfigureVoltageLevel' or (n=='ConfigureOutputEnabled' and a[-1]) for n,a in lib.calls))
        obj.close()

    def test_function_generator_parameters_and_output_control(self):
        lib=FakeDLL('FG33531A');obj=self.open(lib,d.IviCFunctionGenerator,'FG33531A','kt33000')
        obj.configure_waveform('1',1000,2,.1,'square',90)
        self.assertEqual([n for n,_ in lib.calls[-4:]],['ConfigureOutputEnabled','AbortGeneration','ConfigureOutputMode','ConfigureStandardWaveform'])
        self.assertEqual(lib.calls[-1][1][2:],(2,2,.1,1000,90))
        self.assertFalse(any(n=='ConfigureOutputEnabled' and a[-1] for n,a in lib.calls))
        obj.initiate();obj.set_output_enabled('1',True);obj.set_output_enabled('1',False);obj.abort();obj.close()


class FakeCom:
    def __init__(self,model='DPO2002'):
        self.Identity=NS(InstrumentModel=model);self.calls=[]
        self.Channels=self.Measurements=self
        self.Acquisition=NS(ConfigureRecord=self.ConfigureRecord)
    def record(self,name,*args):self.calls.append((name,threading.get_ident(),args))
    def Initialize(self,*args):self.record('init',*args)
    def Item(self,name):self.record('item',name);return self
    def Configure(self,*args):self.record('channel',*args)
    def ConfigureRecord(self,*args):self.record('record',*args)
    def ReadWaveform(self,*args):self.record('read',*args);return ([2.5],0,.01)
    def Abort(self):self.record('abort')
    def Close(self):self.record('close')


class TestCom(unittest.TestCase):
    def test_single_apartment_and_waveform(self):
        fake=FakeCom()
        def create(*args):fake.record('create');return fake,fake,lambda:fake.record('uninit')
        with patch.object(IviComScope,'_create',side_effect=create):
            obj=IviComScope('resource','DPO2002','tekdpo','installed.progid','type.dll','driver.dll')
        obj.configure_channel('CH1',2);obj.configure_record(.1,100)
        self.assertEqual(obj.read_waveform('CH1')['volts'],[2.5]);obj.abort();obj.close();obj.close()
        self.assertEqual(len({x[1] for x in fake.calls}),1)
        self.assertNotEqual(fake.calls[0][1],threading.get_ident())
        self.assertEqual([x[0] for x in fake.calls][-2:],['close','uninit'])
        with self.assertRaises(RuntimeError):obj.abort()

    def test_wrong_com_model_cleanup(self):
        fake=FakeCom('WRONG')
        with patch.object(IviComScope,'_create',return_value=(fake,fake,lambda:fake.record('uninit'))):
            with self.assertRaises(ValueError):IviComScope('r','DPO2002','tekdpo','p','t','d')
        self.assertEqual([x[0] for x in fake.calls][-2:],['close','uninit'])


class TestCatalog(unittest.TestCase):
    def test_every_registry_model_has_actual_class_and_methods(self):
        root=Path(__file__).resolve().parents[1]
        registry=json.loads((root/'unilabos/registry/devices/ivi_family_models.yaml').read_text(encoding='utf-8'))
        models=set()
        for key,r in registry.items():
            pair=(r['manufacturer'],d.normalized(r['model']['name']))
            self.assertNotIn(pair,models);models.add(pair)
            mod,cls=r['class']['module'].split(':');cls=getattr(importlib.import_module(mod),cls)
            cfg=r['init_param_enforce'];family=next(c for c in d.CATALOG if c['id']==cfg['family'])
            d.model_info(cfg['family'],cfg['model'],family['interface'])
            from unilabos.registry.init_enforce import merge_init_param_enforce,validate_init_param_enforce
            validate_init_param_enforce(key,r['init_param_schema'],cfg)
            self.assertEqual(merge_init_param_enforce({'model':'WRONG','family':'WRONG'},cfg),cfg)
            self.assertTrue(r['driver_evidence']['url'].startswith('https://'))
            for method in r['class']['action_value_mappings']:
                self.assertTrue(callable(getattr(cls,method.removeprefix('auto-'))))
        self.assertEqual(len(models),sum(len(c['models']) for c in d.CATALOG))


if __name__=='__main__':unittest.main()
