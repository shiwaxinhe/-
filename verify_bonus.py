"""Loopback mTLS tests for upload/download recovery and shared identity quotas."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
import threading
import time

import checkpoint
from client import Client
from protocol import CHUNK

MIB = 1024 * 1024


def recovery(keys, port, root):
    data = bytes(range(256)) * (2*MIB//256) + b"tail"
    source = root/"sample.bin"
    target = root/"received.bin"
    source.write_bytes(data)
    name = "resume-sample.bin"
    old = b"previous published file"
    target.write_bytes(old)
    seed = Client(keys, "bob", port)
    seed.upload(name, old)
    seed.close()
    for op in ("PUT", "GET"):
        c = Client(keys, "bob", port)
        def stop(offset):
            if offset == 3*CHUNK:
                c.sock.close()
                raise InterruptedError("disconnected after checkpoint")
        def transfer(client, progress=None):
            if op == "PUT":
                return checkpoint.upload(client, name, source, progress)
            return checkpoint.download(client, name, target, progress, root/"cache")
        try:
            transfer(c, stop)
        except InterruptedError:
            pass
        else:
            raise AssertionError("connection not interrupted")
        before = c.sent_body if op=="PUT" else c.received_body
        sid = c.session
        assert before == 3*CHUNK
        other = Client(keys, "bob", port)
        try:
            if op=="PUT":
                assert other.download(name)==old
            else:
                assert target.read_bytes()==old
            sent, received = other.sent_body, other.received_body
            size, offset = transfer(other)
            moved = other.sent_body-sent if op=="PUT" else other.received_body-received
            assert size==len(data) and offset==before and moved==size-offset and other.session!=sid
            result = other.download(name) if op=="PUT" else target.read_bytes()
            assert result==data
            print(f"RESUME {op} total={size} saved={offset} remaining={moved} old={sid[:8]} new={other.session[:8]} sha256={hashlib.sha256(result).hexdigest()}",flush=True)
        finally:
            other.close()

    # Change the remote contents to another file of equal size between downloads.
    c = Client(keys,"bob",port)
    try:
        def stop_local(offset):
            if offset==CHUNK:
                raise InterruptedError
        try:
            checkpoint.download(c,name,target,stop_local,root/"cache")
        except InterruptedError:
            pass
        new = bytes(reversed(data))
        c.upload(name,new)
        assert checkpoint.download(c,name,target,cache=root/"cache")== (len(new),0)
        assert target.read_bytes()==new
        empty=root/"empty"; empty.write_bytes(b"")
        assert checkpoint.upload(c,"bonus-empty",empty)==(0,0)
        assert checkpoint.download(c,"bonus-empty",target,cache=root/"cache")== (0,0)
        assert target.read_bytes()==b""
        print("PASS same-size changed version restarts at zero; empty file round trip",flush=True)
    finally:
        c.close()


def speeds(keys, port, root, rates):
    streams=[(user,i) for user in ("alice","bob") for i in (0,1)]
    size=2*MIB
    for user,i in streams:
        (root/f"{user}-{i}.bin").write_bytes(bytes([i+50])*size)
    for op in ("PUT","GET"):
        ready=threading.Barrier(len(streams))
        def run(spec):
            user,i=spec
            c=Client(keys,user,port)
            try:
                c.list()  # Finish authentication before the timed section.
                name=f"speed-{i}.bin"
                original=root/f"{user}-{i}.bin"
                target=root/f"received-{user}-{i}.bin"
                ready.wait(timeout=20)
                t0=time.perf_counter()
                if op=="PUT":
                    count,start=checkpoint.upload(c,name,original)
                    wire=c.sent_body
                else:
                    count,start=checkpoint.download(c,name,target,cache=root/"cache")
                    wire=c.received_body
                t1=time.perf_counter()
                assert count==wire==size and start==0
                if op=="GET":
                    assert target.read_bytes()==original.read_bytes()
                return user,c.session,t0,t1,wire
            finally:
                c.close()
        with ThreadPoolExecutor(4) as pool:
            rows=list(pool.map(run,streams))
        measured={}
        for user,rate in rates.items():
            pair=[r for r in rows if r[0]==user]
            assert pair[0][1]!=pair[1][1] and max(r[2] for r in pair)<min(r[3] for r in pair)
            elapsed=max(r[3] for r in pair)-min(r[2] for r in pair)
            wire=sum(r[4] for r in pair)
            speed=wire/elapsed/1024
            measured[user]=speed
            print(f"RATE {op} {user} streams=2 bytes={wire} seconds={elapsed:.3f} cap_kib={rate} actual_kib={speed:.1f}",flush=True)
            assert speed <= 1.03*rate, "aggregate cap exceeded"
        assert measured["alice"] > measured["bob"]*1.20, "configured tiers did not separate"


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--keys",default="state/keys")
    parser.add_argument("--port",type=int,default=7443)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        recovery(args.keys,args.port,root)
        speeds(args.keys,args.port,root,{"alice":2048,"bob":1024})
    print("PASS bonus checks",flush=True)
