"""Windows client for the SFX1 file transfer lab."""
import argparse
import queue
from pathlib import Path, PureWindowsPath
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from client import Client
from transfers import Job, Transfers


def local_name(name):
    path = PureWindowsPath(name)
    if (path.name != name or path.is_reserved() or name.endswith((" ", "."))
            or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)):
        raise ValueError(f"无法保存为 Windows 文件名：{name}")
    return name


class Window:
    def __init__(self, root, port=7443, keys="state/keys", host="localhost"):
        self.root, self.port, self.keys, self.host = root, port, keys, host
        self.conn = self.pool = None
        self.pending = set()
        self.checkpoints = {}
        root.title("安全文件传输 · 实验 1B")
        root.geometry("790x490")
        bar = ttk.Frame(root, padding=8)
        bar.pack(fill="x")
        self.user = ttk.Combobox(bar, values=["alice", "bob"], state="readonly", width=8)
        self.user.current(0)
        self.user.pack(side="left")
        actions = [("连接", self.connect), ("刷新", self.refresh), ("上传文件…", self.upload),
                   ("下载所选…", self.download), ("暂停传输", self.pause), ("断开", self.disconnect)]
        for label, action in actions:
            ttk.Button(bar, text=label, command=lambda fn=action: self.run(fn)).pack(side="left", padx=2)
        ttk.Label(root, text="远端文件（Ctrl / Shift 多选）").pack(anchor="w", padx=10)
        self.files = tk.Listbox(root, selectmode="extended", exportselection=False, height=7)
        self.files.pack(fill="both", expand=True, padx=10)
        ttk.Label(root, text="传输记录").pack(anchor="w", padx=10, pady=(8, 0))
        self.history = tk.Listbox(root, height=7, font=("Consolas", 10))
        self.history.pack(fill="both", expand=True, padx=10)
        self.status = tk.StringVar(value="选择证书身份后点击连接。")
        ttk.Label(root, textvariable=self.status, padding=8).pack(anchor="w")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.tick = root.after(80, self.poll)
        self.pulse = root.after(60000, self.heartbeat)

    def run(self, action):
        try:
            if not self.conn and action not in (self.connect, self.disconnect):
                raise ValueError("请先连接服务器")
            action()
        except Exception as exc:
            messagebox.showerror("操作失败", str(exc), parent=self.root)

    def connect(self):
        self.disconnect()
        self.conn = Client(self.keys, self.user.get(), self.port, self.host)
        self.pool = Transfers(self.keys, self.user.get(), self.port, self.host)
        self.user.configure(state="disabled")
        self.refresh()

    def refresh(self):
        names = self.conn.list()
        self.files.delete(0, "end")
        for name in names:
            self.files.insert("end", name)
        self.status.set(f"{self.user.get()}  |  会话 {self.conn.session[:12]}  |  {len(names)} 个文件")

    def heartbeat(self):
        if self.conn:
            try:
                self.conn.list()
            except (OSError, EOFError, ValueError) as exc:
                self.status.set("控制连接中断，请在传输结束后重新连接。 " + str(exc))
        self.pulse = self.root.after(60000, self.heartbeat)

    def enqueue(self, jobs):
        names = [job.name.casefold() for job in jobs]
        if len(set(names)) != len(names) or self.pending.intersection(names):
            raise ValueError("本批或队列中已有同名文件，请等传输结束后再试。")
        if self.pool.paused.is_set() and self.pending:
            raise ValueError("请等正在暂停的任务结束后再重选")
        self.pool.paused.clear()
        for name in names:
            self.checkpoints.pop(name, None)
        self.pending.update(names)
        for job in jobs:
            self.pool.submit(job)
        self.status.set(f"正在传输，剩余 {len(self.pending)} 项")

    def pause(self):
        if self.pending:
            self.pool.paused.set()
            self.status.set("将在分块边界暂停；重选相同文件和目录即可继续。")

    def upload(self):
        paths = list(map(Path, filedialog.askopenfilenames(parent=self.root)))
        if paths:
            self.enqueue([Job("PUT", path.name, path) for path in paths])

    def download(self):
        names = [local_name(self.files.get(i)) for i in self.files.curselection()]
        if not names:
            raise ValueError("请先在远端列表选择文件")
        directory = filedialog.askdirectory(parent=self.root)
        if directory:
            jobs = [Job("GET", name, Path(directory) / name) for name in names]
            if any(job.path.exists() for job in jobs) and not messagebox.askyesno(
                    "确认覆盖", "目标目录中已有同名文件，是否替换？", parent=self.root):
                return
            self.enqueue(jobs)

    def poll(self):
        changed = False
        if self.pool:
            while True:
                try:
                    name, offset = self.pool.progress.get_nowait()
                except queue.Empty:
                    break
                self.checkpoints[name.casefold()] = offset
                self.status.set(f"{name}  |  已保存 {offset:,} B")
            for lane, job, error, sid, size, start, end, resumed in self.pool.completed():
                self.pending.discard(job.name.casefold())
                detail = error or f"{size} B  {sid[:8]}  {(end-start)*1000:.0f} ms"
                if resumed:
                    detail += f"  从 {resumed} B 续传"
                state = "暂停" if error.startswith("暂停于") else ("失败" if error else "完成")
                self.history.insert("end", f"{state}  {lane+1}  {job.operation}  {job.name}  {detail}")
                self.history.see("end")
                changed = True
        if changed:
            if self.pending:
                self.status.set(f"正在传输，剩余 {len(self.pending)} 项")
            else:
                try:
                    self.refresh()
                except (OSError, EOFError, ValueError) as exc:
                    self.status.set("刷新失败：" + str(exc))
        self.tick = self.root.after(80, self.poll)

    def disconnect(self):
        if self.pending:
            raise ValueError("请等待当前传输完成后再断开")
        if self.pool:
            self.pool.close()
            self.pool = None
        if self.conn:
            try:
                self.conn.close()
            except (OSError, EOFError, ValueError):
                self.conn.sock.close()
            self.conn = None
        self.user.configure(state="readonly")
        self.files.delete(0, "end")
        self.history.delete(0, "end")
        self.status.set("已断开")

    def quit(self):
        if self.pending:
            messagebox.showinfo("传输中", "请等待传输结束后关闭窗口。", parent=self.root)
            return
        self.disconnect()
        self.root.after_cancel(self.tick)
        self.root.after_cancel(self.pulse)
        self.root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=7443)
    parser.add_argument("--keys", default="state/keys")
    args = parser.parse_args()
    root = tk.Tk()
    Window(root, args.port, args.keys, args.host)
    root.mainloop()
