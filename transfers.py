"""Two independent TLS sessions shared by the GUI's upload and download queue."""
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from client import Client
import checkpoint


@dataclass(frozen=True)
class Job:
    operation: str
    name: str
    path: Path


class Transfers:
    def __init__(self, keys, user, port, host):
        self.address = keys, user, port, host
        self.events = queue.Queue()
        self.progress = queue.Queue()
        self.queues = [queue.Queue(), queue.Queue()]
        self.loads = [0, 0]
        self.closed = False
        self.paused = threading.Event()
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

    def check_pause(self, offset):
        if self.paused.is_set():
            raise InterruptedError(f'暂停于 {offset} B；重新选择同一文件可续传')

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
                error, sid, size, resumed = "", "", 0, 0
                try:
                    if conn is None:
                        conn = Client(*self.address)
                    self.check_pause(0)
                    def checkpoint_saved(offset):
                        self.progress.put((job.name, offset))
                        self.check_pause(offset)
                    if job.operation == "PUT":
                        size, resumed = checkpoint.upload(conn, job.name, job.path, checkpoint_saved)
                    elif job.operation == "GET":
                        size, resumed = checkpoint.download(conn, job.name, job.path, checkpoint_saved)
                    else:
                        raise ValueError("unknown transfer")
                    sid = conn.session
                except Exception as exc:
                    error = str(exc) or type(exc).__name__
                    if conn:
                        conn.sock.close()
                        conn = None
                self.events.put((lane, job, error, sid, size, start, time.perf_counter(), resumed))
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, EOFError, ValueError):
                    conn.sock.close()
