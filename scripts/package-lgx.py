#!/usr/bin/env python3
"""Package only this project's native module, using the official LGX library.

Run with a trusted liblgx from the Logos Basecamp installation or a reviewed
source build. No dependency installation or signing-policy change is performed. Only the
reviewed module libraries and Python/QML code are included, never wallet state,
model credentials, node_modules, proof logs or host fonts.
"""
from __future__ import annotations
import argparse
import ctypes as c
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODULES = {'commons_relay_module', 'commons_relay_wallet', 'commons_relay_owner_ui'}

class Result(c.Structure):
    _fields_ = [('success', c.c_bool), ('error', c.c_char_p)]
class Verification(c.Structure):
    _fields_ = [('valid',c.c_bool), ('errors',c.POINTER(c.c_char_p)), ('warnings',c.POINTER(c.c_char_p))]

def strings(values):
    out=[]
    if values:
        for i in range(100):
            if not values[i]:break
            out.append(values[i].decode('utf-8','replace'))
    return out

class Lgx:
    def __init__(self,path):
        self.lib=c.CDLL(str(path))
        for name,args,result in [
            ('lgx_load',[c.c_char_p],c.c_void_p),
            ('lgx_add_variant',[c.c_void_p,c.c_char_p,c.c_char_p,c.c_char_p],Result),
            ('lgx_save',[c.c_void_p,c.c_char_p],Result),
            ('lgx_verify',[c.c_char_p],Verification),
            ('lgx_get_last_error',[],c.c_char_p),
            ('lgx_free_package',[c.c_void_p],None),
            ('lgx_free_verify_result',[Verification],None),
        ]:
            f=getattr(self.lib,name);f.argtypes=args;f.restype=result
    def check(self,result):
        if not result.success:raise RuntimeError((result.error or b'LGX operation failed').decode())
    def verify(self,path):
        result=self.lib.lgx_verify(str(path).encode())
        try:return {'valid':bool(result.valid),'errors':strings(result.errors),'warnings':strings(result.warnings)}
        finally:self.lib.lgx_free_verify_result(result)

def files_for_variant(native: Path, variant: str, name: str):
    if name not in MODULES:raise ValueError('Unknown Relay module')
    if variant not in ['darwin-arm64','linux-x86_64']:raise ValueError('Unsupported native variant')
    if native.is_symlink() or not native.is_dir():raise ValueError('Expected a regular staged directory')
    ext='.dylib' if variant=='darwin-arm64' else '.so'
    names=[name+'_plugin'+ext,'metadata.json']
    metadata=json.loads((native/'metadata.json').read_text())
    expected_type='ui_qml' if name=='commons_relay_owner_ui' else 'core'
    if metadata.get('name')!=name or metadata.get('type')!=expected_type or metadata.get('main')!=name+'_plugin':
        raise ValueError('Unexpected module metadata')
    if name=='commons_relay_owner_ui':
        names += [name+'_replica_factory'+ext,'qml/Main.qml','icons/commons_relay.svg','commons_relay_owner_ui.py']
    elif name=='commons_relay_module':names += ['commons_relay_worker.py']
    if name!='commons_relay_wallet':
        package=native/'commons_relay'
        if package.is_symlink() or not (package/'__init__.py').is_file():raise ValueError('Bundled runtime missing')
        for path in sorted(package.rglob('*')):
            if path.is_symlink():raise ValueError('Runtime symlink denied')
            if path.is_file() and path.suffix=='.py':names.append(str(path.relative_to(native)))
    for filename in names:
        path=native/filename
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(native.resolve()):
            raise ValueError('Missing regular staged file: '+filename)
    if any(Path(filename).suffix.lower() in ['.pem','.env','.sqlite','.ttf','.otf','.woff'] for filename in names):
        raise ValueError('Private or host asset in payload')
    return names,metadata

def seed_archive(path:Path,manifest:dict,root_files:dict[str,bytes]):
    with tarfile.open(path,'w:gz',format=tarfile.USTAR_FORMAT) as tar:
        for name,data in {'manifest.json':json.dumps(manifest).encode(),**root_files}.items():
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644;info.mtime=0
            tar.addfile(info,io.BytesIO(data))

def package(lgx:Lgx,native:Path,variant:str,output:Path,name:str):
    names,metadata=files_for_variant(native,variant,name)
    if output.exists():raise ValueError('Refusing to replace an existing package')
    output.parent.mkdir(parents=True,exist_ok=True)
    ext='.dylib' if variant=='darwin-arm64' else '.so'
    manifest={'manifestVersion':'0.3.0','name':name,
              'display_name':metadata.get('display_name',name),'version':metadata['version'],
              'description':metadata.get('description','Commons Relay testnet component'),
              'author':'NotRithik','type':metadata['type'],'category':metadata.get('category','utilities'),
              'dependencies':metadata.get('dependencies',[]),'main':{variant:name+'_plugin'+ext},
              'icon':metadata.get('icon','')}
    for key in ['view']:
        if key in metadata:manifest[key]=metadata[key]
    root_files={}
    with tempfile.TemporaryDirectory(prefix='lgx-build-',dir=output.parent) as temp:
        temp=Path(temp);seed=temp/'seed.lgx';payload=temp/'payload';payload.mkdir()
        for filename in names:
            p=payload/filename;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(native/filename,p)
        for license_name in ['LICENSE-MIT','LICENSE-APACHE']:
            shutil.copyfile(ROOT/license_name,payload/license_name)
        seed_archive(seed,manifest,root_files)
        handle=lgx.lib.lgx_load(str(seed).encode())
        if not handle:raise RuntimeError(lgx.lib.lgx_get_last_error().decode())
        try:
            lgx.check(lgx.lib.lgx_add_variant(handle,variant.encode(),str(payload).encode(),(name+'_plugin'+ext).encode()))
            candidate=temp/'verified.lgx'
            lgx.check(lgx.lib.lgx_save(handle,str(candidate).encode()))
            result=lgx.verify(candidate)
            if not result['valid']:raise RuntimeError('LGX verification failed: '+str(result['errors']))
            candidate.replace(output)
        finally:lgx.lib.lgx_free_package(handle)
    data=output.read_bytes()
    return {'file':output.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
            'variant':variant,'files':names+['LICENSE-MIT','LICENSE-APACHE'],'verification':result}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lgx-lib',type=Path,required=True)
    p.add_argument('--native-dir',type=Path,required=True)
    p.add_argument('--module',choices=sorted(MODULES),required=True)
    p.add_argument('--variant',choices=['darwin-arm64','linux-x86_64'],required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    report=package(Lgx(args.lgx_lib.resolve()),args.native_dir.resolve(),args.variant,args.output.resolve(),args.module)
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
