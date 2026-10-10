"""Native local connection window; file selection stays on this computer."""
from __future__ import annotations
import os
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from connector_core import Connector, ConnectorError, Gateway

STATE_TEXT={
 "stopped":("连接已停止","重新检查本机配置后再授权 Bot。"),
 "not_checked":("尚未检查","先检查已有配置，或选择本机数据库目录和访问材料。"),
 "ready":("数据库可读","已通过本地验证。可以启用本机 Bot 查询；读取范围以查询结果为准。"),
 "ready_to_configure":("材料已验证","点击“连接已验证账号”保存新的本机配置。"),
 "needs_access":("缺少访问材料","选择已有的本人访问材料；此连接器不会自动获取密钥。"),
 "verification_failed":("验证未通过","核对材料是否对应所选账号、数据库是否同步，以及读取依赖。"),
 "existing_configuration_requires_review":("已有配置需要检查","旧配置不会被覆盖。请检查账号、目录和读取依赖。"),
 "dependency_required":("读取依赖未就绪","使用包含读取依赖的安装包，或按 README 准备 SQLCipher/Zstandard。"),
 "unsafe_key_permissions":("材料权限需要收紧","请在本机调整文件权限，再重新验证。"),
 "partial":("部分范围可读","当前不是完整历史，不启用自动查询；请缩小到对应账号目录。"),
 "account_selection_required":("需要选择账号","选择一个明确的本人账号数据库目录，不合并多个账号。"),
 "database_layout_ambiguous":("数据库范围不明确","请选择单一账号的 db_storage 目录。"),
 "acquisition_platform_not_supported":("需要已有访问材料","本版本只导入和验证，不提供自动取钥。"),
 "operation_failed":("操作未完成","检查本机材料、权限与依赖；不会覆盖原有配置。"),
}

class DemoBackend:
    def access(self,config,root=None,source=None,apply=False):
        if apply or not source: return {"state":"ready","live_database_read_ok":True,"counts":{"messages":2}}
        return {"state":"ready_to_configure","live_database_read_ok":False,"counts":{"messages":2}}
    def query(self,config,request):
        return {"schema_version":1,"ok":True,"command":request["command"],
            "data":{"messages":[{"id_source":"wechat_reader","reader_local_id":1,"chat":"示例群","sender":"示例成员","time":"2026-10-10T12:00:00+08:00","content":"这是虚构演示内容。","content_truncated":False}]},
            "coverage":{"source":"wechat_reader","complete":False,"live_refreshed":True,"truncated":False,"warnings":["演示模式，未读取真实微信。"]}}

class ConnectorWindow:
    def __init__(self,root,connector,demo=False):
        self.root,self.connector,self.demo=root,connector,demo
        self.gateway=None
        self.events=queue.Queue()
        self.busy=False
        root.title("微信本地连接器"+(" · 演示" if demo else ""))
        root.geometry("850x740")
        root.minsize(760,720)
        root.configure(bg="#f7f9fc")
        style=ttk.Style()
        style.theme_use("clam")
        style.configure(".",font=("Arial",12),background="#f7f9fc",foreground="#17263c")
        style.configure("TButton",padding=(14,9))
        style.configure("Title.TLabel",font=("Arial",23,"bold"))
        style.configure("Lead.TLabel",font=("Arial",12),foreground="#5b687a")
        style.configure("Status.TLabel",font=("Arial",17,"bold"))
        style.configure("Primary.TButton",foreground="white",background="#146c53")
        outer=ttk.Frame(root,padding=28)
        outer.pack(fill="both",expand=True)
        ttk.Label(outer,text="连接你的微信",style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer,text="密钥保存在这台电脑。Bot 通过授权查询获得消息依据。",style="Lead.TLabel").pack(anchor="w",pady=(8,24))
        if demo:
            ttk.Label(outer,text="演示模式：全部数据虚构，不读取真实账号。",foreground="#9a5b00").pack(anchor="w",pady=(0,12))
        notebook=ttk.Notebook(outer)
        notebook.pack(fill="both",expand=True)
        access=ttk.Frame(notebook,padding=22)
        bot=ttk.Frame(notebook,padding=22)
        notebook.add(access,text="  微信接入  ")
        notebook.add(bot,text="  Bot 连接  ")
        self.status_text=tk.StringVar(value="尚未检查")
        self.detail=tk.StringVar(value=STATE_TEXT["not_checked"][1])
        ttk.Label(access,textvariable=self.status_text,style="Status.TLabel").pack(anchor="w")
        ttk.Label(access,textvariable=self.detail,wraplength=690,style="Lead.TLabel").pack(anchor="w",pady=(8,18))
        reuse=ttk.Frame(access)
        reuse.pack(anchor="w",pady=(0,22))
        self.saved=ttk.Button(reuse,text="恢复连接器已保存账号",command=self.check_saved)
        self.saved.pack(side="left",padx=(0,10))
        self.existing=ttk.Button(reuse,text="复用已有 Reader 配置",command=self.check_existing)
        self.existing.pack(side="left")
        self.pickers=[]
        self.database=tk.StringVar()
        self.source=tk.StringVar()
        self.make_picker(access,"本人账号的数据库目录",self.database,self.choose_database)
        self.make_picker(access,"本机访问材料文件（JSON）",self.source,self.choose_source)
        self.database.trace_add("write",lambda *args:self.changed())
        self.source.trace_add("write",lambda *args:self.changed())
        ttk.Label(access,text="只选择文件，不在界面显示密钥内容。新账号使用独立配置，不覆盖已有 Reader。",wraplength=690,style="Lead.TLabel").pack(anchor="w",pady=(12,16))
        actions=ttk.Frame(access)
        actions.pack(anchor="w")
        self.preview_button=ttk.Button(actions,text="验证所选材料",command=self.preview)
        self.preview_button.pack(side="left",padx=(0,10))
        self.apply_button=ttk.Button(actions,text="连接已验证账号",style="Primary.TButton",command=self.apply,state="disabled")
        self.apply_button.pack(side="left")
        self.progress=ttk.Label(access,text="",style="Lead.TLabel")
        self.progress.pack(anchor="w",pady=(15,0))
        ttk.Label(bot,text="授权本机 Bot 查询",style="Status.TLabel").pack(anchor="w")
        ttk.Label(bot,text="用于同一台电脑上的 Bot 宿主。公网 Bot 不能直接连接此本机地址。",wraplength=690,style="Lead.TLabel").pack(anchor="w",pady=(8,18))
        self.consent=tk.BooleanVar(value=False)
        ttk.Checkbutton(bot,text="允许本机 Bot 只读查询我选定账号的聊天",variable=self.consent).pack(anchor="w")
        self.bind_button=ttk.Button(bot,text="开启连接并生成配对码",style="Primary.TButton",command=self.start_pairing)
        self.bind_button.pack(anchor="w",pady=(18,20))
        self.endpoint=tk.StringVar(value="连接未开启")
        self.code=tk.StringVar(value="")
        self.binding_state=tk.StringVar(value="验证数据库后，再生成配对码。")
        ttk.Label(bot,text="本机地址",style="Lead.TLabel").pack(anchor="w")
        ttk.Entry(bot,textvariable=self.endpoint,state="readonly").pack(fill="x",pady=(6,15))
        ttk.Label(bot,text="一次性配对码 · 2 分钟有效",style="Lead.TLabel").pack(anchor="w")
        row=ttk.Frame(bot)
        row.pack(fill="x",pady=(6,12))
        ttk.Entry(row,textvariable=self.code,state="readonly",font=("Courier",12)).pack(side="left",fill="x",expand=True)
        ttk.Button(row,text="复制配对信息",command=self.copy_pairing).pack(side="left",padx=(10,0))
        ttk.Label(bot,textvariable=self.binding_state,style="Lead.TLabel",wraplength=690).pack(anchor="w",pady=(0,15))
        controls=ttk.Frame(bot)
        controls.pack(anchor="w")
        ttk.Button(controls,text="停止连接",command=self.stop).pack(side="left",padx=(0,10))
        ttk.Button(controls,text="解除 Bot 绑定",command=self.revoke).pack(side="left")
        ttk.Label(outer,text="只读消息 · 不发送微信 · 不生成报告",style="Lead.TLabel").pack(anchor="w",pady=(18,0))
        root.protocol("WM_DELETE_WINDOW",self.close)
        root.after(150,self.tick)
    def make_picker(self,parent,label,variable,command):
        ttk.Label(parent,text=label).pack(anchor="w",pady=(0,6))
        row=ttk.Frame(parent)
        row.pack(fill="x",pady=(0,14))
        entry=ttk.Entry(row,textvariable=variable)
        entry.pack(side="left",fill="x",expand=True)
        button=ttk.Button(row,text="选择…",command=command)
        button.pack(side="left",padx=(10,0))
        self.pickers.extend((entry,button))
    def changed(self):
        if self.busy: return
        self.stop()
        self.apply_button.configure(state="disabled")
    def choose_database(self):
        value=filedialog.askdirectory(title="选择本人账号数据库目录")
        if value: self.changed(); self.database.set(value)
    def choose_source(self):
        value=filedialog.askopenfilename(title="选择本机已有访问材料",filetypes=[("JSON","*.json")])
        if value: self.changed(); self.source.set(value)
    def execute(self,operation):
        if self.busy: return
        self.busy=True
        for button in (self.saved,self.existing,self.preview_button,self.apply_button,self.bind_button,*self.pickers):
            button.configure(state="disabled")
        self.progress.configure(text="正在本机验证，请稍候…")
        def task():
            try: self.events.put(("result",operation()))
            except ConnectorError as exc: self.events.put(("error",exc.message))
            except Exception: self.events.put(("error","操作未完成。原始错误和访问材料未输出。"))
        threading.Thread(target=task,daemon=True).start()
    def check_saved(self):
        self.stop()
        self.execute(lambda:self.connector.use_existing(self.connector.home/"reader.json"))
    def check_existing(self):
        self.stop()
        path=Path(os.environ.get("RION_WECHAT_READER_CONFIG",str(Path.home()/".config/rion-wechat-reader/config.json")))
        self.execute(lambda:self.connector.use_existing(path))
    def preview(self):
        self.stop()
        root,source=self.database.get().strip(),self.source.get().strip()
        self.connector.use_new_profile()
        self.execute(lambda:self.connector.preview(root,source))
    def apply(self):
        root,source=self.database.get().strip(),self.source.get().strip()
        self.execute(lambda:self.connector.apply(root,source))
    def tick(self):
        try:
            kind,value=self.events.get_nowait()
            self.busy=False
            for button in (self.saved,self.existing,self.preview_button,self.bind_button,*self.pickers): button.configure(state="normal")
            if kind=="result":
                title,detail=STATE_TEXT.get(value["state"],("尚未接通","核对账号目录、访问材料和读取依赖；不会自动获取密钥。"))
                self.status_text.set(title); self.detail.set(detail)
                self.apply_button.configure(state="normal" if value["state"]=="ready_to_configure" else "disabled")
            else:
                self.status_text.set("操作未完成"); self.detail.set(value)
                self.apply_button.configure(state="disabled")
            self.progress.configure(text="")
        except queue.Empty: pass
        state=self.connector.status()
        if self.gateway:
            left=state["pairing_expires_in"]
            if state["paired"]:
                self.binding_state.set("Bot 已配对。本次授权在程序退出或解除绑定后失效。")
                self.code.set("")
            elif left:
                self.binding_state.set("等待配对，剩余 "+str(left)+" 秒。")
            else:
                self.binding_state.set("配对码已过期。点击上方按钮重新生成。")
                self.code.set("")
        self.root.after(150,self.tick)
    def start_pairing(self):
        if not self.consent.get():
            messagebox.showinfo("需要授权","勾选允许本机 Bot 查询后再继续。"); return
        try:
            code=self.connector.create_pairing()
            if not self.gateway:
                self.gateway=Gateway(self.connector); self.gateway.start()
            self.endpoint.set("http://127.0.0.1:"+str(self.gateway.port))
            self.code.set(code["code"])
        except (ConnectorError,OSError):
            messagebox.showinfo("尚未开启","请先完成数据库验证，并确认本机端口可用。")
    def copy_pairing(self):
        if not self.code.get(): return
        self.root.clipboard_clear()
        self.root.clipboard_append("地址："+self.endpoint.get()+"\n配对码："+self.code.get())
    def stop(self):
        self.connector.disconnect()
        if self.gateway: self.gateway.stop(); self.gateway=None
        self.status_text.set("连接已停止") if hasattr(self,"status_text") else None
        self.detail.set(STATE_TEXT["stopped"][1]) if hasattr(self,"detail") else None
        self.endpoint.set("连接未开启") if hasattr(self,"endpoint") else None
        self.code.set("") if hasattr(self,"code") else None
    def revoke(self):
        try: self.connector.revoke(); self.code.set(""); self.binding_state.set("绑定已解除。")
        except ConnectorError as exc: messagebox.showinfo("绑定状态",exc.message)
    def close(self):
        self.stop()
        self.root.destroy()

def launch(demo=False):
    temporary=tempfile.TemporaryDirectory() if demo else None
    connector=Connector(Path(temporary.name),DemoBackend()) if demo else Connector()
    root=tk.Tk()
    window=ConnectorWindow(root,connector,demo)
    if demo:
        connector.inspect()
        window.status_text.set("演示数据库可读")
        window.detail.set("可操作配对流程；返回的聊天内容全部虚构。")
    root.mainloop()
    if temporary: temporary.cleanup()
