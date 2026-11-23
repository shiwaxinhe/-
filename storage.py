"""Persistent upload checkpoints and version-pinned reads in the user's namespace."""
import hashlib
import re
import time

from protocol import CHUNK, MAX_FILE

QUOTA = 24 * 1024 * 1024


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS partials(
        owner TEXT, name TEXT, digest TEXT, size INTEGER, data BLOB, updated INTEGER,
        PRIMARY KEY(owner,name))""")


def room(db, table, owner, name, size):
    # Only these two internal queries supply identifiers; external values are bound.
    sql = {"files": "SELECT count(*), coalesce(sum(length(data)),0) FROM files",
           "partials": "SELECT count(*), coalesce(sum(size),0) FROM partials"}[table]
    count, used = db.execute(sql + " WHERE owner=? AND name<>?", (owner, name)).fetchone()
    if count >= 32 or used + size > QUOTA:
        raise ValueError("storage quota")


def resume(db, owner, name, op, req, body):
    key = (owner, name)
    digest = req.get("sha256")
    offset = req.get("offset")
    if op == "GET_META":
        row = db.execute("SELECT digest,length(data) FROM files WHERE owner=? AND name=?", key).fetchone()
        if not row:
            raise ValueError("file not found")
        return {"sha256": row[0], "size": row[1]}, b""
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("invalid digest")
    if op in ("PUT_PART", "GET_PART") and (type(offset) is not int or not 0 <= offset <= MAX_FILE):
        raise ValueError("invalid offset")
    if op == "GET_PART":
        row = db.execute("""SELECT length(data),substr(data,?,?) FROM files
                            WHERE owner=? AND name=? AND digest=?""",
                         (offset+1, CHUNK, *key, digest)).fetchone()
        if not row or offset >= row[0]:
            raise ValueError("file version or offset changed")
        return {"offset": offset}, row[1]
    with db:
        db.execute("BEGIN IMMEDIATE")
        if op == "PUT_OPEN":
            size = req.get("size")
            if type(size) is not int or not 0 <= size <= MAX_FILE:
                raise ValueError("invalid size")
            db.execute("DELETE FROM partials WHERE updated < ?", (int(time.time())-86400,))
            if db.execute("SELECT 1 FROM files WHERE owner=? AND name=? AND digest=? AND length(data)=?",
                          (*key, digest, size)).fetchone():
                db.execute("DELETE FROM partials WHERE owner=? AND name=? AND digest=?", (*key,digest))
                return {"offset": size}, b""
            room(db, "files", *key, size)
            room(db, "partials", *key, size)
            row = db.execute("SELECT digest,size,length(data) FROM partials WHERE owner=? AND name=?", key).fetchone()
            if row and row[:2] == (digest,size):
                db.execute("UPDATE partials SET updated=? WHERE owner=? AND name=?", (int(time.time()), *key))
                return {"offset": row[2]}, b""
            db.execute("INSERT OR REPLACE INTO partials VALUES(?,?,?,?,?,?)", (*key,digest,size,b"",int(time.time())))
            return {"offset": 0}, b""
        if op == "PUT_PART":
            if not 0 < len(body) <= CHUNK:
                raise ValueError("invalid chunk length")
            cur = db.execute("""UPDATE partials SET data=CAST(data || ? AS BLOB),updated=?
                WHERE owner=? AND name=? AND digest=? AND length(data)=? AND size>=?""",
                (body,int(time.time()),*key,digest,offset,offset+len(body)))
            if not cur.rowcount:
                raise ValueError("checkpoint changed; retry")
            return {"offset": offset+len(body)}, b""
        if op != "PUT_DONE":
            raise ValueError("unknown operation")
        row = db.execute("SELECT size,data FROM partials WHERE owner=? AND name=? AND digest=?", (*key,digest)).fetchone()
        if not row:
            if db.execute("SELECT 1 FROM files WHERE owner=? AND name=? AND digest=?", (*key,digest)).fetchone():
                return {"sha256": digest}, b""
            raise ValueError("checkpoint changed; retry")
        size,data = row
        if len(data)!=size or hashlib.sha256(data).hexdigest()!=digest:
            # Remove an unusable complete checkpoint so a fresh upload can recover.
            if len(data)==size:
                db.execute("DELETE FROM partials WHERE owner=? AND name=?",key)
                return {"error":"digest mismatch"}, b""
            raise ValueError("incomplete upload")
        room(db,"files",*key,size)
        db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?)", (*key,digest,data))
        db.execute("DELETE FROM partials WHERE owner=? AND name=?",key)
        return {"sha256":digest}, b""
