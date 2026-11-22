"""Windows desktop client. Launch from the source directory after provisioning."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from client import Client
from protocol import MAX_FILE

class Window:
    def __init__(self,root,port=7443,keys="state/keys"):
        self.root=root
        self.conn=None
        self.port=port
        self.keys=keys
        root.title("SFX1 安全文件传输")
        root.geometry("660x450")
        bar=ttk.Frame(root,padding=12)
        bar.pack(fill="x")
        self.user=ttk.Combobox(bar,values=["alice","bob"],state="readonly",width=9)
        self.user.current(0)
        self.user.pack(side="left")
        for label,action in [("连接",self.connect),("刷新",self.refresh),("上传",self.upload),("下载所选",self.download),("断开",self.disconnect)]:
            ttk.Button(bar,text=label,command=lambda a=action:self.run(a)).pack(side="left",padx=3)
        self.files=tk.Listbox(root,font=("Microsoft YaHei",12))
        self.files.pack(fill="both",expand=True,padx=12)
        self.status=tk.StringVar(value="请选择本地证书身份并连接。")
        ttk.Label(root,textvariable=self.status,padding=12).pack(anchor="w")
        root.protocol("WM_DELETE_WINDOW",self.quit)
        root.after(60000,self.heartbeat)

    def heartbeat(self):
        if self.conn:
            try:
                self.refresh()
            except (OSError,EOFError,ValueError) as exc:
                self.conn.sock.close()
                self.conn=None
                self.user.configure(state="readonly")
                self.files.delete(0,"end")
                self.status.set("连接已中断：" + str(exc))
        self.root.after(60000,self.heartbeat)

    def run(self,action):
        try:
            if not self.conn and action not in (self.connect,self.disconnect):
                raise ValueError("请先连接服务器")
            action()
        except Exception as exc:
            messagebox.showerror("操作失败",str(exc))

    def connect(self):
        self.disconnect()
        self.conn=Client(self.keys,self.user.get(),self.port)
        self.user.configure(state="disabled")
        self.refresh()

    def refresh(self):
        names=self.conn.list()
        self.files.delete(0,"end")
        for name in names:
            self.files.insert("end",name)
        self.status.set(f"{self.user.get()}  |  TLS 1.3  |  会话 {self.conn.session[:12]}  |  {len(names)} 个文件")

    def upload(self):
        name=filedialog.askopenfilename()
        if name:
            path=Path(name)
            with path.open("rb") as f:
                data=f.read(MAX_FILE+1)
            self.conn.upload(path.name,data)
            self.refresh()

    def download(self):
        idx=self.files.curselection()
        if not idx:
            raise ValueError("请先选择文件")
        name=self.files.get(idx[0])
        dest=filedialog.asksaveasfilename(initialfile=Path(name).name)
        if dest:
            Path(dest).write_bytes(self.conn.download(name))
            self.status.set("下载完成，SHA-256 已校验。")

    def disconnect(self):
        try:
            if self.conn:
                self.conn.close()
        finally:
            self.conn=None
            self.user.configure(state="readonly")
            self.files.delete(0,"end")
            self.status.set("已断开")

    def quit(self):
        try:
            self.disconnect()
        finally:
            self.root.destroy()

if __name__=="__main__":
    root=tk.Tk()
    Window(root)
    root.mainloop()

