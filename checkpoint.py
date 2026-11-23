"""Client-side file transfers. Retry the same selection after a disconnect."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from protocol import CHUNK, MAX_FILE


def upload(client, name, path, progress=None):
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    size = len(data)
    if size > MAX_FILE:
        raise ValueError("文件超过 8 MiB")
    digest = hashlib.sha256(data).hexdigest()
    meta, _ = client.request("PUT_OPEN", name=name, sha256=digest, size=size)
    start = offset = meta["offset"]
    if type(offset) is not int or not 0 <= offset <= size:
        raise ValueError("invalid upload checkpoint")
    while offset < size:
        block = data[offset:offset+CHUNK]
        meta, _ = client.request("PUT_PART", block, name=name, sha256=digest, offset=offset)
        offset += len(block)
        if meta["offset"] != offset:
            raise ValueError("incorrect checkpoint receipt")
        if progress:
            progress(offset)
    meta, _ = client.request("PUT_DONE", name=name, sha256=digest)
    if meta["sha256"] != digest:
        raise ValueError("receipt mismatch")
    return size, start


def publish(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sfx-", delete=False) as out:
        temp = Path(out.name)
        try:
            out.write(data)
            out.close()
            os.replace(temp, path)
        finally:
            out.close()
            temp.unlink(missing_ok=True)


def download(client, name, path, progress=None, cache=None):
    meta, _ = client.request("GET_META", name=name)
    digest, size = meta["sha256"], meta["size"]
    if type(size) is not int or not 0 <= size <= MAX_FILE:
        raise ValueError("invalid file size")
    # One database per destination and peer. Short transactions survive process exits.
    if cache is None:
        cache = Path(os.getenv("LOCALAPPDATA") or Path.home()/".cache")/"SFX1"/"checkpoints"
    cache.mkdir(parents=True, exist_ok=True)
    key = json.dumps([client.peer, name, os.path.normcase(str(path.resolve()))], ensure_ascii=False)
    dbpath = cache/(hashlib.sha256(key.encode()).hexdigest()+".sqlite")
    db = sqlite3.connect(dbpath, timeout=10)
    try:
        db.execute("PRAGMA auto_vacuum=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS part(id INTEGER PRIMARY KEY,digest TEXT,data BLOB)")
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT digest,length(data) FROM part WHERE id=1").fetchone()
            if row and row[0] == digest and row[1] <= size:
                start = row[1]
            else:
                db.execute("INSERT OR REPLACE INTO part VALUES(1,?,?)", (digest,b""))
                start = 0
        offset = start
        while offset < size:
            meta, block = client.request("GET_PART", name=name, sha256=digest, offset=offset)
            if meta["offset"] != offset or len(block) != min(CHUNK, size-offset):
                raise ValueError("incorrect download range")
            with db:
                cur = db.execute("""UPDATE part SET data=CAST(data || ? AS BLOB)
                    WHERE id=1 AND digest=? AND length(data)=?""", (block,digest,offset))
                if not cur.rowcount:
                    raise ValueError("local checkpoint changed; retry")
            offset += len(block)
            if progress:
                progress(offset)
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM part WHERE id=1 AND digest=?", (digest,)).fetchone()
            if not row:
                raise ValueError("local checkpoint changed; retry")
            valid = hashlib.sha256(row[0]).hexdigest() == digest
            if valid:
                publish(path, row[0])
            db.execute("DELETE FROM part WHERE id=1")
        if not valid:
            raise ValueError("download digest mismatch; retry from zero")
        return size, start
    finally:
        db.close()
