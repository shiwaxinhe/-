"""SFX1 frame format: magic | JSON length | binary length | JSON | binary."""
import json
import socket
import ssl
import struct
import time
from pathlib import Path

HEADER = struct.Struct("!4sII")
MAX_META = 8192
MAX_FILE = 8 * 1024 * 1024
CHUNK = 256 * 1024

def exact(sock, size, deadline, pace=None):
    buf = bytearray()
    while len(buf) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("frame deadline")
        sock.settimeout(remaining)
        chunk = sock.recv(min(65536, size-len(buf)))
        if not chunk:
            raise EOFError("connection closed")
        buf.extend(chunk)
        if pace:
            deadline += pace(len(chunk))
    return bytes(buf)

def receive(sock, pace=None, frame_timeout=20):
    sock.settimeout(600)
    first = sock.recv(1)
    if not first:
        raise EOFError("connection closed")
    deadline = time.monotonic()+frame_timeout
    magic, m, n = HEADER.unpack(first+exact(sock, HEADER.size-1, deadline))
    if magic != b"SFX1" or not 0 < m <= MAX_META or n > MAX_FILE:
        raise ValueError("bad frame")
    meta = json.loads(exact(sock,m,deadline))
    if not isinstance(meta,dict):
        raise ValueError("bad metadata")
    return meta, exact(sock,n,deadline,pace)

def send(sock, meta, body=b"", pace=None):
    encoded=json.dumps(meta,ensure_ascii=False,separators=(",",":")).encode("utf-8")
    if len(encoded)>MAX_META or len(body)>MAX_FILE:
        raise ValueError("frame too large")
    sock.settimeout(20)
    sock.sendall(HEADER.pack(b"SFX1",len(encoded),len(body))+encoded)
    for offset in range(0,len(body),65536):
        block=memoryview(body)[offset:offset+65536]
        if pace:
            pace(len(block))
        sock.sendall(block)

def context(keys, identity, server=False):
    keys=Path(keys)
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version=ssl.TLSVersion.TLSv1_3
    ctx.load_verify_locations(cafile=str(keys/"ca.pem"))
    ctx.verify_mode=ssl.CERT_REQUIRED
    ctx.load_cert_chain(str(keys/(identity+".pem")),str(keys/(identity+".key")))
    return ctx

def keepalive(sock):
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_KEEPALIVE,1)
    sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)

