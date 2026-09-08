#!/usr/bin/env python3
"""Send a JSON command to an installed Relay module via local Logos Core IPC."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from commons_relay.codec import parse,Rejected
from commons_relay.control import CoreClient

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--logosctl',type=Path,required=True);parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=120)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--command-file',type=Path);group.add_argument('--method')
    parser.add_argument('--params',default='{}');args=parser.parse_args()
    if args.command_file:
        if args.command_file.is_symlink() or args.command_file.stat().st_size>60000:parser.error('Expected a bounded regular command file')
        command=parse(args.command_file.read_bytes())
    else:command={'method':args.method,'params':parse(args.params.encode())}
    if not isinstance(command,dict) or set(command)!={'method','params'}:parser.error('Command must contain method and params')
    client=CoreClient(args.logosctl,args.session,timeout=args.timeout)
    try:result=client.request(command['method'],command['params'])
    except Rejected as error:
        print(json.dumps({'success':False,'error':str(error)}));raise SystemExit(1)
    print(json.dumps({'success':True,'result':result},indent=2))
if __name__=='__main__':main()
