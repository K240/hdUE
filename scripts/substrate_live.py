"""Asynchronous, single-flight preparation of immutable Substrate variants."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
from substrate_graph import signature,COMPILER_VERSION


class LivePreparation:
    def __init__(self,runtime_project,log_directory,root=None,debounce=.65):
        self.root=Path(root) if root else Path(__file__).resolve().parents[1]
        self.runtime_project=Path(runtime_project).resolve()
        self.namespace=f'/Game/HdUE/Live/P{os.getpid()}_{uuid.uuid4().hex[:12]}'
        self.directory=Path(log_directory)/'substrate-live'/self.namespace.rsplit('/',1)[1]
        self.debounce=debounce; self.enabled=True; self.closed=False
        self.entries={}; self.failures={}; self.wanted=None; self.changed_at=0.; self.active=None
        self.revision=0; self.started=0; self.completed=0; self.phase='idle'; self.message=''
        self.last_progress=0.

    @staticmethod
    def identity(record): return (signature(record['graph']),record.get('base_identity',''))

    def status(self):
        return dict(phase=self.phase,message=self.message,started=self.started,completed=self.completed,
                    worker_pid=self.active['process'].pid if self.active else None,
                    wanted=self.wanted[0] if self.wanted else None,active=self.active['key'] if self.active else None,
                    cache_entries=len(self.entries),revision=self.revision)

    def tick(self):
        if not self.active: return
        active=self.active; code=active['process'].poll()
        if code is None:
            now=time.monotonic()
            if now-self.last_progress<.2: return
            self.last_progress=now
            try:
                progress=json.loads((active['folder']/'progress.json').read_text(encoding='utf-8-sig'))
                self.phase=progress['phase']; self.message=progress['message']
            except (OSError,ValueError,KeyError): pass
            return
        active['log'].close(); self.active=None
        try:
            if code: raise ValueError('Substrate preparation failed; see '+str(active['folder']/'worker.log'))
            receipt=json.loads((active['output']/'published.json').read_text(encoding='utf-8-sig'))
            data=json.loads((active['output']/'library.json').read_text(encoding='utf-8-sig'))
            if not receipt.get('inline_shader_code') or data.get('compiler')!=COMPILER_VERSION:
                raise ValueError('The live material has an incompatible preparation receipt')
            for entry in data['materials']:
                self.entries[(entry['signature'],entry.get('base_identity',''))]=entry
            self.revision+=1; self.completed+=1; self.phase='ready'; self.message='Edited Substrate material is ready'
        except (OSError,ValueError,KeyError) as error:
            self.failures[active['key']]=str(error); self.phase='failed'; self.message=str(error)

    def request(self,records,known,now=None):
        self.tick()
        if not self.enabled or self.closed: return None
        identities={(e['signature'],e.get('base_identity','')) for e in known}
        missing=[r for r in records if self.identity(r) not in identities]
        if not missing: return None
        keys=sorted({self.identity(r) for r in missing})
        key=hashlib.sha256(json.dumps(keys).encode()).hexdigest()
        now=time.monotonic() if now is None else now
        if self.wanted is None or self.wanted[0]!=key:
            self.wanted=(key,missing); self.changed_at=now
        if key in self.failures: return self.failures[key]
        if self.active:
            suffix='; latest edit is queued' if self.active['key']!=key else ''
            return 'Substrate: '+(self.message or 'Preparing the edited graph')+suffix
        if now-self.changed_at<self.debounce:
            self.phase='queued'; self.message='Waiting for graph edits to settle'
            return 'Substrate: '+self.message
        self._start(key,missing)
        return 'Substrate: Preparing the edited graph in the background'

    def _start(self,key,records):
        executable=shutil.which('pwsh') or shutil.which('powershell')
        if not executable:
            self.phase='failed'; self.message='PowerShell is required for automatic Substrate preparation'
            self.failures[key]=self.message; return
        self.started+=1
        folder=self.directory/f'{self.started:04d}-{key[:12]}'
        job=folder/'job.json'; output=folder/'prepared'
        destination=self.namespace+f'/V{self.started}_{key[:12]}'
        log=None
        command=[executable,'-NoProfile','-NonInteractive','-File',str(self.root/'scripts/Prepare-SubstrateLive.ps1'),
                 '-GraphJob',str(job.resolve()),'-OutputDirectory',str(output.resolve()),'-Destination',destination,
                 '-RuntimeProject',str(self.runtime_project)]
        # A GUI host's USD plugin paths/startup hooks must not leak into hython
        # or the editor worker. Inherited hooks can load another backend/USD ABI.
        environment={name:value for name,value in os.environ.items() if not (
            name.startswith(('HOUDINI_','PXR_','QT_')) or
            (name.startswith('HDUE_HOUDINI_') and name!='HDUE_HOUDINI_ROOT') or
            name in ('HFS','PYTHONHOME','PYTHONPATH','HDUE_BACKEND_DLL','HDUE_RUNTIME_CONFIG'))}
        try:
            folder.mkdir(parents=True,exist_ok=False)
            job.write_text(json.dumps(dict(version=1,materials=records),indent=2),encoding='utf-8')
            log=(folder/'worker.log').open('wb')
            process=subprocess.Popen(command,cwd=self.root,stdout=log,stderr=subprocess.STDOUT,
                                     env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except OSError as error:
            if log: log.close()
            self.failures[key]=str(error); self.phase='failed'; self.message=str(error); return
        self.active=dict(key=key,process=process,folder=folder,output=output,log=log)
        self.phase='starting'; self.message='Starting background Substrate preparation'

    def retry(self):
        self.failures.clear(); self.changed_at=0.

    def close(self):
        self.closed=True
        # Do not interrupt an editor while it is saving/cooking packages. It can
        # finish its immutable output safely after the owning viewport closes.
        if self.active: self.active['log'].close()
