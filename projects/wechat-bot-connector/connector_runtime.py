#!/usr/bin/env python3
"""GUI launcher and isolated Reader workers; no key acquisition routes."""
from __future__ import annotations
import argparse
import json
import getpass
from pathlib import Path
import subprocess
import sys
import time
from connector_core import ConnectorError, query_args, runtime_command, safe_access

def read_input():
    data=sys.stdin.read(16385)
    if len(data)>16384: raise ValueError
    value=json.loads(data)
    if not isinstance(value,dict): raise ValueError
    return value

def access_worker():
    import rion_wechat_access as access
    value=read_input()
    if set(value)-{"config","root","source","apply"}: raise ValueError
    if not isinstance(value.get("config"),str) or type(value.get("apply")) is not bool: raise ValueError
    for name in ("root","source"):
        if value.get(name) is not None and not isinstance(value[name],str): raise ValueError
    args=argparse.Namespace(config=Path(value["config"]),database_root=Path(value["root"]) if value.get("root") else None,
        source=Path(value["source"]) if value.get("source") else None,
        apply=value["apply"],provider=None,sha256="",confirm_reviewed_provider=False,
        confirm_side_effects=False,max_files=300,timeout=60)
    result=access.onboard(args)
    print(json.dumps({"ok":True,"data":safe_access(result)},ensure_ascii=False))
    return 0

def query_worker():
    import wechat_query as query
    value=read_input()
    if set(value)!={"config","request"} or not isinstance(value["config"],str): raise ValueError
    argv=query_args(value["request"])
    config=value["config"]
    def reader_call(args, command, deadline):
        remaining=deadline-time.monotonic()
        if remaining<=0: raise query.QueryError("reader_timeout","读取超时。")
        try:
            result=subprocess.run(runtime_command()+["--worker","reader","--config",config,"--strict-read-only",*command],
                capture_output=True,text=True,encoding="utf-8",timeout=remaining,shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
            if result.returncode or len(result.stdout)>10_000_000: raise ValueError
            payload=json.loads(result.stdout)
            if not isinstance(payload,dict) or payload.get("ok") is not True: raise ValueError
            return payload.get("data")
        except subprocess.TimeoutExpired:
            raise query.QueryError("reader_timeout","读取超时；请缩小查询范围。") from None
        except (OSError,ValueError,UnicodeError):
            raise query.QueryError("reader_failed","Reader 读取失败；原始日志未返回。") from None
    query.reader_call=reader_call
    return query.main(argv)

def worker(action):
    try:
        if action=="access": return access_worker()
        if action=="query": return query_worker()
        if action=="reader":
            import rion_wechat_reader as reader
            return reader.main(sys.argv[3:])
        raise ValueError
    except Exception as exc:
        # Do not print exception strings, account paths, keys or tracebacks.
        category="dependency_required" if isinstance(exc,ImportError) else "worker_filesystem_failed" if isinstance(exc,OSError) else "worker_input_failed" if isinstance(exc,ValueError) else "worker_type_failed" if isinstance(exc,TypeError) else "worker_attribute_failed" if isinstance(exc,AttributeError) else "operation_failed"
        code=getattr(exc,"code",category)
        if type(exc).__module__=="rion_wechat_access" and type(exc).__name__=="AccessError":
            known={"symlink_path_rejected","database_root_missing","account_selection_required","reader_config_exists_not_overwritten","config_directory_not_private","reader_verification_failed","key_environment_override_requires_removal"}
            if exc.args and exc.args[0] in known: code=exc.args[0]
        if not isinstance(code,str) or len(code)>80 or not all(c.isalnum() or c=="_" for c in code): code="operation_failed"
        print(json.dumps({"ok":False,"error":{"code":code,"message":"本地读取未完成，请检查材料、权限和运行依赖。"}},ensure_ascii=False))
        return 2

def main():
    # Console executables on Windows may inherit a non-UTF-8 code page.
    # Every worker uses the same UTF-8 pipe contract, including nested Reader calls.
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if stream is not None and hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if len(sys.argv)>2 and sys.argv[1]=="--worker":
        return worker(sys.argv[2])
    parser=argparse.ArgumentParser(description="WeChat local connector")
    parser.add_argument("--mcp",action="store_true",help="MCP stdio tools for an existing host")
    parser.add_argument("--pair-host",action="store_true",help="Pair a native host; code is entered privately")
    parser.add_argument("--host-query",action="store_true",help="Read a JSON tool request from stdin")
    parser.add_argument("--host-config",action="store_true",help="Print token-free MCP launch configuration")
    parser.add_argument("--demo",action="store_true",help="Synthetic UI and pairing only; no real account")
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--self-test-ui",action="store_true",help="Open and close a synthetic window; no account read")
    args=parser.parse_args()
    modes=(args.mcp,args.pair_host,args.host_query,args.host_config,args.self_test,args.self_test_ui,args.demo)
    if sum(modes)>1:
        print(json.dumps({"ok":False,"error":{"code":"invalid_request","message":"一次只选择一个运行模式。"}}))
        return 2
    if args.mcp:
        from host_mcp import serve
        return serve(sys.stdin.buffer,sys.stdout)
    if args.pair_host or args.host_query or args.host_config:
        from host_client import HostClient, HostError
        client=HostClient()
        try:
            if args.pair_host:
                code=getpass.getpass("输入连接器生成的配对码（不显示）：")
                result=client.pair(code)
            elif args.host_config:
                result=client.descriptor(runtime_command()+["--mcp"])
            else:
                request=read_input()
                result=client.status() if request=={"command":"status"} else client.query(request)
            print(json.dumps(result,ensure_ascii=False))
            return 0
        except (HostError,ValueError,RecursionError,EOFError,KeyboardInterrupt) as exc:
            print(json.dumps({"ok":False,"error":{"code":getattr(exc,"code","invalid_request"),
                "message":"本机工具未完成，请检查连接器状态、查询参数和配对。"}},ensure_ascii=False))
            return 2
    if args.self_test:
        import tkinter
        import rion_wechat_reader
        import rion_wechat_access
        import wechat_query
        from sqlcipher3 import dbapi2 as sqlcipher
        import zstandard
        with sqlcipher.connect(":memory:") as database:
            cipher_version=database.execute("PRAGMA cipher_version").fetchone()[0]
        print(json.dumps({"ok":True,"tk_version":tkinter.TkVersion,"sqlcipher":cipher_version,"zstandard":zstandard.__version__,"mode":"no_account_read"}))
        return 0
    from connector_app import launch
    launch(demo=args.demo or args.self_test_ui,auto_close_ms=500 if args.self_test_ui else None)
    if args.self_test_ui: print(json.dumps({"ok":True,"mode":"synthetic_window"}))
    return 0
if __name__=="__main__":
    raise SystemExit(main())
