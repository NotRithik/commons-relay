#!/usr/bin/env python3
"""Run one pinned RISC Zero prover behind SSH stdin/stdout; never log the stream.

The local listener is ephemeral and loopback-only. This transports private
proving inputs: the SSH worker must be trusted, even though wallet files stay
on the client. The client must verify every returned receipt.
"""
from pathlib import Path
import fcntl,hashlib,json,os,socket,stat,subprocess,sys,threading,time
CONFIG=Path('/opt/kite/prover/prover.json')

def checked_prover():
    meta=CONFIG.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o022 or meta.st_size>4096:
        raise RuntimeError('INVALID_PROVER_CONFIGURATION')
    value=json.loads(CONFIG.read_text())
    if set(value)!={'executable','sha256'}:raise RuntimeError('INVALID_PROVER_CONFIGURATION')
    path=Path(value['executable']);meta=path.lstat()
    if not path.is_absolute() or not stat.S_ISREG(meta.st_mode) or meta.st_mode&0o022 or not os.access(path,os.X_OK):
        raise RuntimeError('INVALID_PROVER_EXECUTABLE')
    with path.open('rb') as source:
        actual=hashlib.file_digest(source,'sha256').hexdigest() if hasattr(hashlib,'file_digest') else hashlib.sha256(source.read()).hexdigest()
    if actual!=value['sha256']:raise RuntimeError('PROVER_BINARY_CHANGED')
    return str(path)

def main():
    executable=checked_prover()
    env={'PATH':'/usr/local/cuda-12.9/bin:/usr/bin:/bin','HOME':'/root',
         'LD_LIBRARY_PATH':'/usr/local/cuda-12.9/lib64:/usr/lib/wsl/lib',
         'RISC0_DEV_MODE':'0','RUST_LOG':'warn','RAYON_NUM_THREADS':'4'}
    if sys.argv[1:]==['--version']:
        result=subprocess.run([executable,'--version'],env=env,timeout=30)
        return result.returncode
    if sys.argv[1:]:raise RuntimeError('INVALID_BRIDGE_ARGUMENTS')
    lock_fd=os.open(CONFIG.parent/'.gpu-proof.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('GPU_PROVER_BUSY') from None
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.5)
    port=listener.getsockname()[1]
    child=subprocess.Popen([executable,'--port',str(port)],env=env,stdin=subprocess.DEVNULL,stdout=sys.stderr,stderr=sys.stderr,start_new_session=True)
    connection=None
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if child.poll() is not None:raise RuntimeError('PROVER_EXITED_BEFORE_CONNECT')
            try:connection,_=listener.accept();break
            except socket.timeout:continue
        if connection is None:raise RuntimeError('PROVER_CONNECT_TIMEOUT')
        listener.close();connection.settimeout(None)
        print('[Kite prover] Real-proof IPC connected; RISC0_DEV_MODE=0.',file=sys.stderr,flush=True)
        def send_input():
            try:
                while True:
                    data=os.read(sys.stdin.fileno(),65536)
                    if not data:break
                    connection.sendall(data)
            except (OSError,ValueError):pass
            finally:
                try:connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                if child.poll() is None:child.terminate()
        threading.Thread(target=send_input,daemon=True).start()
        while True:
            data=connection.recv(65536)
            if not data:break
            view=memoryview(data)
            while view:
                count=os.write(sys.stdout.fileno(),view)
                view=view[count:]
        try:return child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.terminate();return child.wait(timeout=10)
    finally:
        listener.close()
        os.close(lock_fd)
        if connection is not None:connection.close()
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('[Kite prover] Bridge failed: '+type(error).__name__+' '+str(error)[:120],file=sys.stderr)
        raise SystemExit(1)
