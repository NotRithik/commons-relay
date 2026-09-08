#!/usr/bin/env python3
"""Fetch only immutable official source revisions. This executes no repository code."""
import argparse,json,pathlib,subprocess
PINS={
 'logos-view-module':'d6c8885494524504cc5ea9dcfdad54a98ed26ea4',
 'logos-plugin-qt':'6990e588c4ef127c7ed1810814fd0c2f6fad880b',
 'logos-protocol':'cac0642a4f230818b9891e71d38a815bafe6c2b2',
}
def fetch(destination):
 destination.mkdir(parents=True,exist_ok=True)
 for name,revision in PINS.items():
  target=destination/name
  if target.is_symlink():raise RuntimeError('Refusing symlink checkout')
  if not target.exists():
   target.mkdir()
   subprocess.run(['git','-c','core.hooksPath=/dev/null','init',str(target)],check=True)
   subprocess.run(['git','-C',str(target),'remote','add','origin',f'https://github.com/logos-co/{name}.git'],check=True)
  remote=subprocess.check_output(['git','-C',str(target),'remote','get-url','origin'],text=True).strip()
  if remote!=f'https://github.com/logos-co/{name}.git':raise RuntimeError(f'Unexpected source remote for {name}')
  dirty=subprocess.check_output(['git','-C',str(target),'status','--porcelain'],text=True)
  if dirty:raise RuntimeError(f'Refusing to overwrite modified checkout {target}')
  subprocess.run(['git','-C',str(target),'-c','core.hooksPath=/dev/null','fetch','--depth','1','origin',revision],check=True,timeout=180)
  subprocess.run(['git','-C',str(target),'-c','core.hooksPath=/dev/null','checkout','--detach',revision],check=True)
  actual=subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True).strip()
  if actual!=revision:raise RuntimeError('Revision mismatch')
  print(name,actual,flush=True)
 (destination/'source-pins.json').write_text(json.dumps(PINS,indent=2)+'\n')
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('destination',type=pathlib.Path)
 args=parser.parse_args();fetch(args.destination.resolve())
