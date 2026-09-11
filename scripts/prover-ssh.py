#!/usr/bin/env python3
"""RISC0_SERVER_PATH adapter for a trusted SSH worker using loopback IPC.

Only --version and --port are accepted. Configuration lives next to this file.
The stream is never decoded, saved or printed. Do not use an untrusted worker:
proving inputs can contain secrets even though no wallet file is copied.
"""
from pathlib import Path
import json,os,re,socket,stat,subprocess,sys,threading

def ssh_command(version):
    config=Path(__file__).with_suffix('.json');meta=config.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode&0o022 or meta.st_size>4096:
        raise RuntimeError('INVALID_SSH_PROVER_CONFIG')
    v=json.loads(config.read_text())
    if set(v)!={'target','identity_file','known_hosts_file','distribution','remote_bridge'}:
        raise RuntimeError('INVALID_SSH_PROVER_CONFIG')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+',v['target']):raise RuntimeError('INVALID_SSH_TARGET')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',v['distribution']):raise RuntimeError('INVALID_WSL_DISTRIBUTION')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',v['remote_bridge']) or '..' in Path(v['remote_bridge']).parts:
        raise RuntimeError('INVALID_REMOTE_BRIDGE')
    for name in ('identity_file','known_hosts_file'):
        p=Path(v[name])
        if not p.is_absolute() or p.is_symlink() or not p.is_file():raise RuntimeError('INVALID_SSH_FILE')
    cmd=['/usr/bin/ssh','-T','-F','/dev/null','-i',v['identity_file'],
         '-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','BatchMode=yes',
         '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+v['known_hosts_file'],
         '-o','ConnectTimeout=10','-o','ServerAliveInterval=20','-o','ServerAliveCountMax=3',v['target'],
         'wsl.exe','-d',v['distribution'],'-u','root','--','/usr/bin/python3',v['remote_bridge']]
    if version:cmd.append('--version')
    return cmd

def main():
    args=sys.argv[1:]
    if args==['--version']:
        return subprocess.run(ssh_command(True),timeout=45).returncode
    if len(args)!=2 or args[0]!='--port' or not args[1].isdigit() or not 1024<=int(args[1])<=65535:
        raise RuntimeError('INVALID_PROVER_ARGUMENTS')
    connection=socket.create_connection(('127.0.0.1',int(args[1])),timeout=10);connection.settimeout(None)
    child=subprocess.Popen(ssh_command(False),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr,bufsize=0)
    try:
        def outgoing():
            try:
                while True:
                    data=connection.recv(65536)
                    if not data:break
                    view=memoryview(data)
                    while view:
                        count=os.write(child.stdin.fileno(),view);view=view[count:]
            except (OSError,ValueError):pass
            finally:
                try:child.stdin.close()
                except OSError:pass
        threading.Thread(target=outgoing,daemon=True).start()
        while True:
            data=os.read(child.stdout.fileno(),65536)
            if not data:break
            connection.sendall(data)
        try:connection.shutdown(socket.SHUT_WR)
        except OSError:pass
        return child.wait(timeout=20)
    finally:
        connection.close()
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('[Kite SSH prover] '+type(error).__name__+': '+str(error)[:120],file=sys.stderr)
        raise SystemExit(1)
