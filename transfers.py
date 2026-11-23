"""Two independent TLS sessions shared by the GUI's upload and download queue."""
import os
import queue
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from client import Client
from protocol import MAX_FILE


@dataclass(frozen=True)
class Job:
    operation: str
    name: str
    path: Path


def save_verified(path, data):
    # The caller has already verified the digest. Keep an existing file on I/O failure.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sfx-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            os.replace(temporary, path)
        finally:
            handle.close()
            temporary.unlink(missing_ok=True)


class Transfers:
    def __init__(self, keys, user, port, host):
        self.address = keys, user, port, host
        self.events = queue.Queue()
        self.queues = [queue.Queue(), queue.Queue()]
        self.loads = [0, 0]
        self.closed = False
        self.threads = []
        for lane, inbox in enumerate(self.queues):
            thread = threading.Thread(target=self.work, args=(lane, inbox), daemon=True)
            thread.start()
            self.threads.append(thread)

    def submit(self, job):
        if self.closed:
            raise RuntimeError("queue closed")
        lane = self.loads.index(min(self.loads))
        self.loads[lane] += 1
        self.queues[lane].put(job)

    def completed(self):
        while True:
            try:
                lane, *result = self.events.get_nowait()
            except queue.Empty:
                return
            self.loads[lane] -= 1
            yield lane, *result

    def close(self):
        self.closed = True
        for inbox in self.queues:
            inbox.put(None)
        for thread in self.threads:
            thread.join()

    def work(self, lane, inbox):
        conn = None
        try:
            while True:
                try:
                    job = inbox.get(timeout=60)
                except queue.Empty:
                    if conn:
                        try:
                            conn.list()
                        except (OSError, EOFError, ValueError):
                            conn.sock.close()
                            conn = None
                    continue
                if job is None:
                    return
                start = time.perf_counter()
                error, sid, size = "", "", 0
                try:
                    if conn is None:
                        conn = Client(*self.address)
                    if job.operation == "PUT":
                        with job.path.open("rb") as handle:
                            data = handle.read(MAX_FILE + 1)
                        if len(data) > MAX_FILE:
                            raise ValueError("文件超过 8 MiB")
                        conn.upload(job.name, data)
                    elif job.operation == "GET":
                        data = conn.download(job.name)
                        save_verified(job.path, data)
                    else:
                        raise ValueError("unknown transfer")
                    size = len(data)
                    sid = conn.session
                except Exception as exc:
                    error = str(exc) or type(exc).__name__
                    if conn:
                        conn.sock.close()
                        conn = None
                self.events.put((lane, job, error, sid, size, start, time.perf_counter()))
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, EOFError, ValueError):
                    conn.sock.close()
