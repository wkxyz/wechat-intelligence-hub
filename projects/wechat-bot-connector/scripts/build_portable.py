#!/usr/bin/env python3
"""Build a native portable runtime with the read-only Reader dependencies."""
from pathlib import Path
import platform
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
PROJECT=ROOT/"projects/wechat-bot-connector"
name="wechat-connector"
args=[sys.executable,"-m","PyInstaller","--noconfirm","--clean","--onedir","--console",
      "--name",name,"--distpath",str(ROOT/"dist"),"--workpath",str(ROOT/"build"),
      "--specpath",str(ROOT/"build"),"--paths",str(PROJECT),
      "--paths",str(ROOT/"projects/rion-wechat-reader"),
      "--paths",str(ROOT/"projects/wechat-intelligence-hub"),
      "--hidden-import","connector_app","--hidden-import","rion_wechat_access",
      "--hidden-import","rion_wechat_reader","--hidden-import","wechat_query",
      "--hidden-import","message_time","--hidden-import","sqlcipher3.dbapi2",
      "--collect-all","sqlcipher3","--collect-all","zstandard"]
if sys.platform=="win32": args+=["--hide-console","hide-early"]
args.append(str(PROJECT/"connector_runtime.py"))
subprocess.run(args,check=True)
folder=ROOT/"dist"/name
(folder/"README.md").write_bytes((PROJECT/"README.md").read_bytes())
for license_name in ("LICENSE","NOTICE"):
    if (ROOT/license_name).is_file():
        (folder/license_name).write_bytes((ROOT/license_name).read_bytes())
(folder/"platform.txt").write_text(platform.system()+" "+platform.machine()+"\n",encoding="utf-8")
if sys.platform=="darwin":
    launcher=folder/"启动微信连接器.command"
    launcher.write_text('#!/bin/sh\ncd "$(dirname "$0")"\nexec ./wechat-connector "$@"\n')
    launcher.chmod(0o755)
print(folder)
