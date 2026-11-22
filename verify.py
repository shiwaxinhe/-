"""Run benign end-to-end acceptance checks against a fresh lab database."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import socket
import ssl
import threading
from pathlib import Path
from client import Client
from protocol import MAX_FILE,receive,send

def verify(keys,port):
    together=threading.Barrier(2)
    def roundtrip(user):
        c=Client(keys,user,port)
        try:
            together.wait(timeout=10)
            name="课程样本.bin"
            size=1048576 if user=="alice" else 65537
            data=(user.encode()*size)[:size]
            c.upload(name,data)
            assert name in c.list()
            assert c.download(name)==data
            print(f"PASS {user}  {size} bytes  SHA256={hashlib.sha256(data).hexdigest()}  session={c.session[:12]}",flush=True)
        finally:
            c.close()
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(roundtrip,["alice","bob"]))
    a=Client(keys,"alice",port)
    b=Client(keys,"bob",port)
    try:
        name="仅 Alice's 实验.txt"
        a.upload(name,b"")
        assert a.download(name)==b"" and name not in b.list()
        try:
            b.download(name)
        except ValueError as err:
            assert str(err)=="file not found"
        else:
            raise AssertionError("identity isolation")
        print("PASS empty file, Unicode, apostrophe and private namespaces",flush=True)
        try:
            a.request("PUT",b"bad",name=name,sha256="0"*64)
        except ValueError as err:
            assert str(err)=="digest mismatch"
        else:
            raise AssertionError("incorrect checksum accepted")
        assert a.download(name)==b""
        block=b"1"*MAX_FILE
        a.upload("limit1",block)
        a.upload("limit2",block)
        try:
            a.upload("limit3",block)
        except ValueError as err:
            assert str(err)=="storage quota"
        else:
            raise AssertionError("quota")
        assert "limit3" not in a.list()
        print("PASS failed overwrite preserves original; 8 MiB file; 24 MiB quota",flush=True)
    finally:
        a.close()
        b.close()
    ctx=ssl.create_default_context(cafile=str(Path(keys)/"ca.pem"))
    with socket.create_connection(("127.0.0.1",port),5) as raw:
        try:
            with ctx.wrap_socket(raw,server_hostname="localhost") as s:
                send(s,{"op":"LIST"})
                receive(s)
        except (ssl.SSLError,EOFError,ConnectionError):
            print("PASS unauthenticated client denied",flush=True)
        else:
            raise AssertionError("client certificate missing")
    ctx.load_cert_chain(str(Path(keys)/"alice.pem"),str(Path(keys)/"alice.key"))
    with socket.create_connection(("127.0.0.1",port),5) as raw:
        try:
            with ctx.wrap_socket(raw,server_hostname="wrong.example"):
                pass
        except ssl.SSLCertVerificationError:
            print("PASS wrong server hostname rejected",flush=True)
        else:
            raise AssertionError("hostname not checked")
    print("PASS all checks",flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=7443)
    parser.add_argument("--keys",default="state/keys")
    args=parser.parse_args()
    verify(args.keys,args.port)

