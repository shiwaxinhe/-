import argparse
import hashlib
from pathlib import Path
import socketserver
import sqlite3
import threading
import uuid
from protocol import context, keepalive, receive, send

QUOTA = 24 * 1024 * 1024

class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, keys, db):
        self.tls = context(keys, "server", server=True)
        self.db = str(db)
        self.slots = threading.BoundedSemaphore(8)
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS files(owner TEXT,name TEXT,digest TEXT,data BLOB,PRIMARY KEY(owner,name))")
            conn.commit()
        finally:
            conn.close()
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.slots.release()

def dispatch(conn, owner, req, data):
    op = req.get("op")
    if op != "PUT" and data:
        return {"error":"unexpected body"}, b""
    if op == "LIST":
        names = [row[0] for row in conn.execute("SELECT name FROM files WHERE owner=? ORDER BY name",(owner,))]
        return {"files":names}, b""
    if op == "QUIT":
        return {"bye":True}, b""
    if op not in ("PUT","GET"):
        return {"error":"unknown operation"}, b""
    name = req.get("name")
    if not isinstance(name,str) or not 1<=len(name.encode("utf-8"))<=96 or name in (".","..") or any(ord(c)<32 or c in '/\\' for c in name):
        return {"error":"invalid name"}, b""
    if op == "GET":
        row=conn.execute("SELECT digest,data FROM files WHERE owner=? AND name=?",(owner,name)).fetchone()
        if not row:
            return {"error":"file not found"}, b""
        return {"sha256":row[0]},row[1]
    digest=hashlib.sha256(data).hexdigest()
    if req.get("sha256")!=digest:
        return {"error":"digest mismatch"},b""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        count,used=conn.execute("SELECT count(*),COALESCE(sum(length(data)),0) FROM files WHERE owner=? AND name<>?",(owner,name)).fetchone()
        if count>=32 or used+len(data)>QUOTA:
            return {"error":"storage quota"},b""
        conn.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?)",(owner,name,digest,data))
    return {"sha256":digest},b""

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        keepalive(self.request)
        try:
            with self.server.tls.wrap_socket(self.request,server_side=True) as sock:
                subject=dict(pair for part in sock.getpeercert()["subject"] for pair in part)
                owner=subject.get("commonName")
                if owner not in ("alice","bob"):
                    return
                session=uuid.uuid4().hex
                conn=sqlite3.connect(self.server.db,timeout=10)
                try:
                    while True:
                        req,body=receive(sock)
                        response,reply=dispatch(conn,owner,req,body)
                        send(sock,dict(response,session=session,owner=owner),reply)
                        if response.get("bye"):
                            break
                finally:
                    conn.close()
        except (OSError,EOFError,ValueError,RecursionError):
            return

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=7443)
    parser.add_argument("--bind",default="127.0.0.1")
    parser.add_argument("--keys",default="state/keys")
    parser.add_argument("--db",default="state/data/files.sqlite")
    args=parser.parse_args()
    with Server((args.bind,args.port),args.keys,args.db) as server:
        print("READY",server.server_address[1],flush=True)
        server.serve_forever()

