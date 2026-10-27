"""Queue ordering, retries, environment isolation, and immutable publication."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from substrate_live import LivePreparation
from substrate_graph import signature
from prepare_substrate import publish


def record(extra=False,value=.5):
    nodes={'surface':dict(type='slab',inputs={'roughness':value})}
    if extra:
        nodes['value']=dict(type='constant',properties={'value':value})
        nodes['surface']['inputs']['roughness']={'node':'value'}
    return dict(material='/Looks/Test',graph=dict(version=1,output='surface',nodes=nodes),base_identity='',base_asset='')


class Process:
    pid=42
    def __init__(self,*args,**kwargs): self.code=None
    def poll(self): return self.code


class LiveTests(unittest.TestCase):
    def test_single_flight_latest_graph_cache_failure_and_retry(self):
        with tempfile.TemporaryDirectory() as folder, patch('substrate_live.shutil.which',return_value='pwsh'), \
                patch('substrate_live.subprocess.Popen',side_effect=Process) as popen:
            root=Path(folder); manager=LivePreparation(root/'runtime/HdUEBackend.uproject',root/'logs',root,debounce=.5)
            a=record(); b=record(True)
            manager.request([a],[],now=0); self.assertEqual(manager.started,0)
            manager.request([a],[],now=1); self.assertEqual(manager.started,1)
            manager.request([record(value=.8)],[],now=1.1); self.assertEqual(manager.started,1)
            manager.request([b],[],now=1.2); self.assertEqual(manager.started,1)
            active=manager.active; active['output'].mkdir()
            entry=dict(signature=signature(a['graph']),base_identity='',asset='/Game/A.A',preview_asset='/Game/AP.AP')
            (active['output']/'published.json').write_text(json.dumps(dict(inline_shader_code=True)))
            (active['output']/'library.json').write_text(json.dumps(dict(compiler=2,materials=[entry])))
            active['process'].code=0; manager.tick()
            self.assertEqual(manager.completed,1); self.assertEqual(len(manager.entries),1)
            # A completed stale job is cached, while B remains the requested graph.
            self.assertEqual(manager.wanted[0],__import__('hashlib').sha256(json.dumps([(signature(b['graph']),'')]).encode()).hexdigest())
            manager.request([b],list(manager.entries.values()),now=2); self.assertEqual(manager.started,2)
            manager.active['process'].code=1; manager.tick()
            self.assertIn('failed',manager.request([b],[],now=3))
            self.assertEqual(manager.started,2)
            manager.retry(); manager.request([b],[],now=4); self.assertEqual(manager.started,3)
            self.assertIsNone(manager.request([a],list(manager.entries.values()),now=5))
            manager.close()
            self.assertEqual(popen.call_count,3)

    def test_worker_does_not_inherit_gui_usd_startup_environment(self):
        with tempfile.TemporaryDirectory() as folder, patch('substrate_live.shutil.which',return_value='pwsh'), \
                patch('substrate_live.subprocess.Popen',side_effect=Process) as popen, \
                patch.dict(os.environ,{'HOUDINI_PATH':'gui-hooks','PXR_PLUGINPATH_NAME':'gui-usd',
                    'PYTHONPATH':'gui-python','HDUE_HOUDINI_ENABLE':'1','HDUE_HOUDINI_ROOT':'sdk-root'}):
            manager=LivePreparation(Path(folder)/'runtime/HdUEBackend.uproject',Path(folder)/'logs',Path(folder),debounce=0)
            manager.request([record()],[],now=0)
            environment=popen.call_args.kwargs['env']
            for key in ('HOUDINI_PATH','PXR_PLUGINPATH_NAME','PYTHONPATH','HDUE_HOUDINI_ENABLE'): self.assertNotIn(key,environment)
            self.assertEqual(environment['HDUE_HOUDINI_ROOT'],'sdk-root')
            manager.close()

    def test_unavailable_worker_is_latched_until_retry(self):
        with tempfile.TemporaryDirectory() as folder, patch('substrate_live.shutil.which',return_value=None):
            manager=LivePreparation(Path(folder)/'runtime/HdUEBackend.uproject',Path(folder)/'logs',Path(folder),debounce=0)
            manager.request([record()],[],now=0)
            self.assertEqual(manager.phase,'failed'); self.assertEqual(manager.started,0)
            self.assertIn('PowerShell',manager.request([record()],[],now=1))
            self.assertEqual(manager.started,0)
            manager.close()

    def test_publication_is_atomic_immutable_and_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); output=root/'job'; project=root/'runtime/HdUEBackend.uproject'
            project.parent.mkdir(); project.write_text('{}')
            destination='/Game/HdUE/Live/Session/V1'
            source=output/'cooked/Windows/HdUEBackend/Content/HdUE/Live/Session/V1'
            source.mkdir(parents=True)
            for name in ('M.uasset','M.uexp','P.uasset'): (source/name).write_bytes(b'owned fixture')
            library=dict(version=1,compiler=2,materials=[dict(asset=destination+'/M.M',preview_asset=destination+'/P.P')])
            (output/'library.json').write_text(json.dumps(library))
            publish(output,project,destination)
            self.assertEqual((project.parent/'Content/HdUE/Live/Session/V1/M.uexp').read_bytes(),b'owned fixture')
            self.assertTrue((output/'published.json').is_file())
            with self.assertRaises(FileExistsError): publish(output,project,destination)
            with self.assertRaises(ValueError): publish(output,project,'/Game/HdUE/Live/../../Outside')


if __name__=='__main__': unittest.main()
