"""Exercise stream framing and the same two transfer workers used by the GUI."""
import argparse
import hashlib
import json
import struct
import tempfile
import time
from collections import deque
from pathlib import Path

from client import Client
from protocol import receive
from transfers import Job, Transfers


class Segments:
    def __init__(self, segments):
        self.segments = deque(segments)

    def settimeout(self, value):
        pass

    def recv(self, size):
        if not self.segments:
            return b""
        piece = self.segments.popleft()
        if len(piece) > size:
            self.segments.appendleft(piece[size:])
        return piece[:size]


def check_framing():
    def frame(op, body):
        meta = json.dumps({"op": op}).encode()
        return struct.pack("!4sII", b"SFX1", len(meta), len(body)) + meta + body
    first = frame("PUT", b"a\x00z")
    raw = first + frame("LIST", b"")
    stream = Segments([raw[:2], raw[2:10], raw[10:len(first)-1], raw[len(first)-1:]])
    assert receive(stream) == ({"op": "PUT"}, b"a\x00z")
    assert receive(stream) == ({"op": "LIST"}, b"")
    print("PASS fragmented header + two adjacent frames; binary NUL preserved")


def collect(pool, jobs):
    for job in jobs:
        pool.submit(job)
    deadline = time.monotonic() + 15
    results = []
    while len(results) < len(jobs):
        results.extend(pool.completed())
        if time.monotonic() > deadline:
            raise AssertionError("workers timed out")
        time.sleep(0.01)
    return sorted(results, key=lambda result: result[0])


def verify(keys, port):
    check_framing()
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        get_path, put_path = base / "received.bin", base / "local.bin"
        inbound = bytes(range(256)) * 4096
        outbound = bytes(reversed(range(256))) * 4096
        put_path.write_bytes(outbound)
        control = Client(keys, "bob", port)
        pool = Transfers(keys, "bob", port, "localhost")
        try:
            control.upload("batch-source.bin", inbound)
            jobs = [Job("PUT", "batch-upload.bin", put_path), Job("GET", "batch-source.bin", get_path)]
            rounds = []
            for attempt in (1, 2):
                if attempt==2:
                    # A different digest forces a second full upload on the same session.
                    outbound=b"\x00"+outbound[1:]
                    put_path.write_bytes(outbound)
                results = collect(pool, jobs)
                assert all(not row[2] for row in results), results
                assert all(row[7]==0 and row[4]==len(inbound) for row in results)
                ids = [row[3] for row in results]
                assert len(set(ids + [control.session])) == 3
                overlap = min(row[6] for row in results) - max(row[5] for row in results)
                assert overlap > 0, "worker task intervals did not overlap"
                assert get_path.read_bytes() == inbound and control.download(jobs[0].name) == outbound
                rounds.append(ids)
                print(f"PASS mixed round={attempt} PUT={ids[0][:12]} GET={ids[1][:12]} overlap_ms={overlap*1000:.1f} bytes_each={len(inbound)}")
            assert rounds[0] == rounds[1]
            print("PASS same two TLS sessions reused in both rounds; received SHA256=" +
                  hashlib.sha256(inbound).hexdigest())
            old = get_path.read_bytes()
            failed, = collect(pool, [Job("GET", "absent-batch-file.bin", get_path)])
            assert failed[2] == "file not found" and get_path.read_bytes() == old
            print("PASS missing source reported; existing local file preserved")
        finally:
            pool.close()
            control.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", default="state/keys")
    parser.add_argument("--port", type=int, default=7443)
    args = parser.parse_args()
    verify(args.keys, args.port)
