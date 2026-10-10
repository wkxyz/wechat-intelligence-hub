"""Private local onboarding and a loopback-only tool gateway."""
from __future__ import annotations
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

PROJECTS = Path(__file__).resolve().parent.parent
for project in ("rion-wechat-reader", "wechat-intelligence-hub"):
    sys.path.insert(0, str(PROJECTS / project))
MAX_BODY = 16384
DEFAULT_PORT = 17673

class ConnectorError(Exception):
    def __init__(self, code, message="操作未完成，请检查连接状态。"):
        self.code, self.message = code, message
        super().__init__(message)

def runtime_command():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return [str(executable), str(Path(__file__).with_name("connector_runtime.py"))]

def run_worker(action, payload, timeout=75):
    try:
        result = subprocess.run(runtime_command()+["--worker", action],
            input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
        if len(result.stdout)>10_000_000:
            raise ValueError
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except subprocess.TimeoutExpired:
        raise ConnectorError("timeout", "验证或查询超时，请缩小数据库范围。") from None
    except (OSError, UnicodeError, ValueError):
        raise ConnectorError("worker_failed", "读取组件未返回有效结果，请检查运行依赖。") from None

class RuntimeBackend:
    def access(self, config, root=None, source=None, apply=False):
        result=run_worker("access", {"config":str(config),"root":root,"source":source,"apply":apply})
        if not result.get("ok"):
            raise ConnectorError(result.get("error",{}).get("code","access_failed"),"本地验证未完成，请检查材料与依赖。")
        return result["data"]
    def query(self, config, request):
        return run_worker("query", {"config":str(config),"request":request}, timeout=35)

def safe_access(value):
    state=str(value.get("state","unknown"))
    live_ok=value.get("live_database_read_ok") is True
    result={"state":state,"live_database_read_ok":live_ok,"can_query":state=="ready" and live_ok,
        "configured":bool(value.get("configured")),"reused_existing_configuration":bool(value.get("reused_existing_configuration")),
        "recovery_review_required":bool(value.get("recovery_review_required")),"counts":{}}
    for key,number in (value.get("counts") or {}).items():
        if key in ("messages","session","contact","databases","verified_database_count","matched_database_count","unresolved_database_count") and type(number) is int:
            result["counts"][key]=number
    return result

def private_write(path, value):
    # Reuse the Reader's POSIX/Windows private-file implementation.
    import rion_wechat_reader as reader
    reader.secure_write_json(path,value,force=True)

def query_args(request):
    import wechat_query as query
    if not isinstance(request,dict):
        raise ConnectorError("invalid_request")
    allowed={"command","query","keywords","chat","since","until","limit","max_text_chars","live"}
    if set(request)-allowed or request.get("command") not in ("search","topic","history","reply"):
        raise ConnectorError("invalid_request","只允许检索、主题、历史和回复证据查询。")
    if "live" in request and request["live"] is not True:
        raise ConnectorError("invalid_request","连接器只提供实时只读查询。")
    argv=[request["command"],"--live"]
    for key in ("query","chat","since","until","limit","max_text_chars"):
        if key in request:
            value=request[key]
            if key in ("limit","max_text_chars"):
                if type(value) is not int:
                    raise ConnectorError("invalid_request")
            elif not isinstance(value,str):
                raise ConnectorError("invalid_request")
            argv += ["--"+key.replace("_","-"),str(value)]
    words=request.get("keywords",[])
    if not isinstance(words,list) or not all(isinstance(word,str) for word in words):
        raise ConnectorError("invalid_request")
    for word in words: argv += ["--keyword",word]
    try:
        query.parse_args(argv)
    except query.QueryError:
        raise ConnectorError("invalid_request","查询参数或时间范围无效。") from None
    return argv

class Connector:
    def __init__(self, home=None, backend=None, clock=time.monotonic):
        self.home=Path(home or os.environ.get("WECHAT_CONNECTOR_HOME",str(Path.home()/".config/wechat-bot-connector"))).expanduser()
        self.config=self.home/"reader.json"
        self.backend=backend or RuntimeBackend()
        self.clock=clock
        self.lock=threading.RLock()
        self.query_lock=threading.Lock()
        self.ready=False
        self.preview_selection=None
        self.pair_digest=None
        self.pair_deadline=0
        self.pair_failures=[]
        self.token_digest=None
        self.queries=[]
        self.version=0
        self.last_status={"state":"not_checked","can_query":False,"counts":{}}
        # Device sessions intentionally expire when the connector is restarted.
        # Only a digest/audit record is persisted; no key or token is restored.

    def disconnect(self):
        with self.lock:
            self.ready=False
            self.token_digest=None
            self.pair_digest=None
            self.preview_selection=None
            self.last_status={"state":"stopped","can_query":False,"counts":{}}
            self.version+=1

    def generation(self):
        with self.lock: return self.version

    def _check_generation(self, expected_version):
        if expected_version is not None and expected_version!=self.version:
            raise ConnectorError("operation_cancelled","连接状态已改变，本次验证结果已取消。")

    def _access(self, root=None, source=None, apply=False, expected_version=None):
        with self.lock:
            self._check_generation(expected_version)
            version,config=self.version,self.config
        try:
            value=safe_access(self.backend.access(config,root,source,apply))
        except ConnectorError:
            with self.lock:
                if version==self.version: self.disconnect()
            raise
        with self.lock:
            if version!=self.version or config!=self.config:
                raise ConnectorError("operation_cancelled","连接状态已改变，本次验证结果已取消。")
            self.last_status=value
            self.ready=value["can_query"]
            self.version+=1
        return value

    def inspect(self, expected_version=None):
        with self.lock:
            self._check_generation(expected_version)
            self.disconnect()
            version=self.version
        return self._access(expected_version=version)

    def use_existing(self, config, expected_version=None):
        with self.lock:
            self._check_generation(expected_version)
            self.disconnect()
            self.config=Path(config).expanduser()
            version=self.version
        return self._access(expected_version=version)

    def use_new_profile(self):
        self.disconnect()
        with self.lock:
            self.config=self.home/"reader.json"

    def preview(self, root, source, expected_version=None):
        with self.lock:
            self._check_generation(expected_version)
            self.disconnect()
            if not root or not isinstance(root,str) or not source or not isinstance(source,str):
                raise ConnectorError("selection_required","请选择数据库目录和本机材料文件。")
            version=self.version
        value=self._access(root,source,False,expected_version=version)
        with self.lock:
            self._check_generation(version+1)
            if value["state"]=="ready_to_configure":
                self.preview_selection=(root,source)
        return value

    def apply(self, root, source, expected_version=None):
        with self.lock:
            self._check_generation(expected_version)
            if self.preview_selection!=(root,source):
                raise ConnectorError("preview_required","先验证当前选择的材料，再连接。")
            self.preview_selection=None
            version=self.version
        return self._access(root,source,True,expected_version=version)

    def create_pairing(self):
        with self.lock:
            if not self.ready: raise ConnectorError("not_ready","先完成本地数据库验证。")
            self.token_digest=None
            self.version+=1
            code=secrets.token_urlsafe(18)
            self.pair_digest=hashlib.sha256(code.encode()).hexdigest()
            self.pair_deadline=self.clock()+120
            self.pair_failures=[]
            return {"code":code,"expires_in":120}

    def pair(self,code):
        with self.lock:
            now=self.clock()
            self.pair_failures=[t for t in self.pair_failures if now-t<60]
            if len(self.pair_failures)>=5: raise ConnectorError("rate_limited","配对尝试过多，请稍后重试。")
            if not isinstance(code,str) or len(code)>128: raise ConnectorError("unauthorized","配对码无效。")
            digest=hashlib.sha256(code.encode()).hexdigest()
            if not self.ready or not self.pair_digest or now>=self.pair_deadline or not hmac.compare_digest(digest,self.pair_digest):
                self.pair_failures.append(now)
                raise ConnectorError("unauthorized","配对码无效或已过期。")
            token=secrets.token_urlsafe(32)
            token_digest=hashlib.sha256(token.encode()).hexdigest()
            try:
                private_write(self.home/"binding.json",{"schema_version":1,"token_hash":token_digest,"session":"expires_on_restart"})
            except Exception:
                raise ConnectorError("private_storage_failed","无法安全保存本地绑定记录，未完成配对。") from None
            self.token_digest=token_digest
            self.pair_digest=None
            self.version+=1
            return {"ok":True,"device_token":token,"session":"expires_on_restart"}

    def authorize(self,token):
        with self.lock:
            return isinstance(token,str) and len(token)<=128 and bool(self.token_digest) and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),self.token_digest)

    def revoke(self):
        with self.lock:
            self.token_digest=None
            self.pair_digest=None
            self.version+=1
            try:
                private_write(self.home/"binding.json",{"schema_version":1,"token_hash":None,"session":"revoked"})
            except Exception:
                # Old records are never loaded as authentication on startup.
                self.ready=False
                raise ConnectorError("private_storage_failed","已停止本次授权，但无法更新绑定记录。") from None

    def status(self):
        with self.lock:
            return {"ok":True,"reader":dict(self.last_status),"can_query":self.ready,
                "paired":bool(self.token_digest),"pairing_expires_in":max(0,int(self.pair_deadline-self.clock())) if self.pair_digest else 0}

    def query(self,token,request):
        if not self.authorize(token): raise ConnectorError("unauthorized","请先配对。")
        query_args(request)
        with self.lock:
            if not self.ready: raise ConnectorError("not_ready","本地连接已停止。")
            now=self.clock()
            self.queries=[t for t in self.queries if now-t<3600]
            if len(self.queries)>=60: raise ConnectorError("rate_limited","本机每小时最多 60 次查询。")
            self.queries.append(now)
            version=self.version
        if not self.query_lock.acquire(blocking=False):
            raise ConnectorError("busy","已有查询进行中，请等待它结束。")
        try:
            value=self.backend.query(self.config,{**request,"live":True})
            with self.lock:
                if not self.ready or version!=self.version or not self.authorize(token):
                    raise ConnectorError("unauthorized","查询期间连接或授权已改变，结果未返回。")
            return value
        finally:
            self.query_lock.release()

class Gateway:
    def __init__(self,connector,port=DEFAULT_PORT):
        self.connector=connector
        self.server=ThreadingHTTPServer(("127.0.0.1",port),self._handler())
        self.server.daemon_threads=True
        self.port=self.server.server_port
        self.thread=None
    def _handler(self):
        connector=self.connector
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)
            def log_message(self,*args):
                pass
            def send_json(self,status,value):
                body=json.dumps(value,ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type","application/json; charset=utf-8")
                self.send_header("Cache-Control","no-store")
                self.send_header("X-Content-Type-Options","nosniff")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                try:
                    host=self.headers.get_all("Host") or []
                    if host!=["127.0.0.1:"+str(self.server.server_port)] or self.headers.get("Origin") is not None:
                        return self.send_json(403,{"ok":False,"error":{"code":"origin_denied"}})
                    if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length") or [])!=1:
                        return self.send_json(400,{"ok":False,"error":{"code":"invalid_request"}})
                    length=int(self.headers.get("Content-Length","0"))
                    if length>MAX_BODY: return self.send_json(413,{"ok":False,"error":{"code":"request_too_large"}})
                    if length<=0 or self.headers.get("Content-Type","").split(";")[0].lower()!="application/json":
                        raise ConnectorError("invalid_request")
                    payload=json.loads(self.rfile.read(length))
                    if self.path=="/pair":
                        if not isinstance(payload,dict) or set(payload)!={"code"}: raise ConnectorError("invalid_request")
                        return self.send_json(200,connector.pair(payload["code"]))
                    header=self.headers.get("Authorization","")
                    token=header[7:] if header.startswith("Bearer ") else None
                    if not connector.authorize(token): raise ConnectorError("unauthorized","请先配对。")
                    if self.path=="/query": value=connector.query(token,payload)
                    elif self.path=="/status": value=connector.status()
                    else: return self.send_json(404,{"ok":False,"error":{"code":"not_found"}})
                    self.send_json(200,value)
                except ConnectorError as exc:
                    status={"unauthorized":401,"not_ready":503,"busy":429,"rate_limited":429}.get(exc.code,400)
                    self.send_json(status,{"ok":False,"error":{"code":exc.code,"message":exc.message}})
                except (ValueError,TypeError,UnicodeError):
                    self.send_json(400,{"ok":False,"error":{"code":"invalid_request"}})
                except (TimeoutError,OSError):
                    self.close_connection=True
                except Exception:
                    self.send_json(500,{"ok":False,"error":{"code":"internal_error","message":"查询未完成。"}})
            def do_GET(self):
                self.send_json(405,{"ok":False,"error":{"code":"method_not_allowed"}})
            do_OPTIONS=do_GET
        return Handler
    def start(self):
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
    def stop(self):
        if self.thread:
            self.server.shutdown()
            self.thread.join(timeout=2)
        self.server.server_close()
