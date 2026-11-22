import hashlib
import socket
from protocol import context,keepalive,receive,send

class Client:
    def __init__(self,keys,user,port=7443,host="localhost"):
        ctx=context(keys,user)
        raw=socket.create_connection((host,port),5)
        try:
            keepalive(raw)
            self.sock=ctx.wrap_socket(raw,server_hostname=host)
        except BaseException:
            raw.close()
            raise
        self.session=None

    def request(self,op,body=b"",**fields):
        send(self.sock,dict(fields,op=op),body)
        meta,reply=receive(self.sock)
        if self.session is not None and self.session!=meta["session"]:
            raise ValueError("session changed")
        self.session=meta["session"]
        if "error" in meta:
            raise ValueError(meta["error"])
        return meta,reply

    def upload(self,name,data):
        digest=hashlib.sha256(data).hexdigest()
        meta,_=self.request("PUT",data,name=name,sha256=digest)
        if meta["sha256"]!=digest:
            raise ValueError("receipt mismatch")
        return digest

    def download(self,name):
        meta,data=self.request("GET",name=name)
        if hashlib.sha256(data).hexdigest()!=meta["sha256"]:
            raise ValueError("download digest mismatch")
        return data

    def list(self):
        return self.request("LIST")[0]["files"]

    def close(self):
        try:
            self.request("QUIT")
        finally:
            self.sock.close()

