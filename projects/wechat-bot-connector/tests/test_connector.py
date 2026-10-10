"""Synthetic connection and pairing checks. Never reads a real account."""
import http.client
import importlib.util
import os
import gc
import subprocess
import threading
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connector_core import Connector, ConnectorError, Gateway, RuntimeBackend, safe_access
if os.environ.get("CONNECTOR_TEST_EXECUTABLE"):
    import connector_core
    executable=str(Path(os.environ["CONNECTOR_TEST_EXECUTABLE"]).resolve())
    connector_core.runtime_command=lambda:[executable]

class FakeBackend:
    def __init__(self):
        self.calls = []
        self.state = "ready_to_configure"
    def access(self, config, root=None, source=None, apply=False):
        self.calls.append((str(config), root, source, apply))
        return {"state": "ready" if apply else self.state,
                "live_database_read_ok": apply or self.state == "ready",
                "counts": {"messages": 2}, "raw_key": "synthetic-secret"}
    def query(self, config, request):
        self.calls.append(dict(request))
        return {"schema_version":1,"ok":True,"data":{"messages":[{"chat":"示例群","sender":"示例成员","time":"2026-10-10T12:00:00+08:00","content":"示例内容"}]},
                "coverage":{"complete":False,"source":"wechat_reader"}}

class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name).resolve()
        if sys.platform=="win32":
            import rion_wechat_reader as reader
            self.assertTrue(reader.windows_private_acl(self.home,initialize=True))
        self.backend = FakeBackend()
        self.now = 1000.0
        self.connector = Connector(self.home, self.backend, clock=lambda:self.now)
        self.gateway = None
    def tearDown(self):
        if self.gateway:
            self.gateway.stop()
        self.temp.cleanup()
    def ready(self):
        self.backend.state = "ready"
        self.connector.inspect()
    def test_preview_does_not_apply_and_hides_material(self):
        p = self.connector.preview("/synthetic/db", "/synthetic/access.json")
        self.assertFalse(self.backend.calls[-1][-1])
        self.assertEqual(p["state"], "ready_to_configure")
        self.assertNotIn("raw_key", p)
        self.assertFalse(p["can_query"])
    def test_failed_preview_cannot_be_applied(self):
        self.backend.state = "verification_failed"
        self.connector.preview("/synthetic/db", "/synthetic/access.json")
        with self.assertRaises(ConnectorError):
            self.connector.apply("/synthetic/db", "/synthetic/access.json")
        self.assertFalse(any(call[-1] for call in self.backend.calls))
    def test_changed_selection_requires_new_preview(self):
        self.connector.preview("/synthetic/db", "/synthetic/access.json")
        with self.assertRaises(ConnectorError):
            self.connector.apply("/different/db", "/synthetic/access.json")
    def test_apply_uses_verified_selection_without_overwrite_flag(self):
        self.connector.preview("/synthetic/db", "/synthetic/access.json")
        p = self.connector.apply("/synthetic/db", "/synthetic/access.json")
        self.assertTrue(p["can_query"])
        self.assertTrue(self.backend.calls[-1][-1])
    def test_binding_requires_ready_connection(self):
        with self.assertRaises(ConnectorError):
            self.connector.create_pairing()
    def test_pairing_is_one_use_and_returns_device_token_not_wechat_key(self):
        self.ready()
        code = self.connector.create_pairing()["code"]
        token = self.connector.pair(code)["device_token"]
        self.assertTrue(self.connector.authorize(token))
        with self.assertRaises(ConnectorError):
            self.connector.pair(code)
        state = (self.home/"binding.json").read_text()
        self.assertNotIn(token, state)
        self.assertNotIn(code, state)
        self.assertNotIn("synthetic-secret", state)
    def test_new_pairing_revokes_previous_device(self):
        self.ready()
        token=self.connector.pair(self.connector.create_pairing()["code"])["device_token"]
        code=self.connector.create_pairing()["code"]
        self.assertFalse(self.connector.authorize(token))
        self.assertTrue(self.connector.pair(code)["ok"])
    def test_pairing_expires(self):
        self.ready()
        code = self.connector.create_pairing()["code"]
        self.now += 121
        with self.assertRaises(ConnectorError):
            self.connector.pair(code)
    def test_pairing_wrong_code_is_bounded(self):
        self.ready()
        self.connector.create_pairing()
        for _ in range(5):
            with self.assertRaises(ConnectorError):
                self.connector.pair("wrong")
        with self.assertRaises(ConnectorError) as result:
            self.connector.pair("wrong")
        self.assertEqual(result.exception.code, "rate_limited")
    def test_revoke_invalidates_token_immediately_and_after_restart(self):
        self.ready()
        token = self.connector.pair(self.connector.create_pairing()["code"])["device_token"]
        self.connector.revoke()
        self.assertFalse(self.connector.authorize(token))
        restored = Connector(self.home, self.backend)
        self.assertFalse(restored.authorize(token))
    def test_binding_file_is_private_on_posix(self):
        self.ready()
        self.connector.pair(self.connector.create_pairing()["code"])
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE((self.home/"binding.json").stat().st_mode), 0o600)
    def test_query_only_allows_safe_fields_and_forces_live(self):
        self.ready()
        token = self.connector.pair(self.connector.create_pairing()["code"])["device_token"]
        self.connector.query(token, {"command":"search","query":"示例"})
        self.assertTrue(self.backend.calls[-1]["live"])
        for request in [
            {"command":"sql","query":"drop table"},
            {"command":"search","query":"x","reader":"/bin/sh"},
            {"command":"search","query":"x","db":"/private/path"},
            {"command":"search","query":"x","live":False},
            {"command":"search","query":"x","limit":101},
        ]:
            with self.subTest(request=request):
                with self.assertRaises(ConnectorError):
                    self.connector.query(token,request)
    def start_gateway(self):
        self.ready()
        self.gateway = Gateway(self.connector, port=0)
        self.gateway.start()
    def request(self, path, payload, token=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway.port, timeout=3)
        h={"Content-Type":"application/json", **(headers or {})}
        if token: h["Authorization"]="Bearer "+token
        conn.request("POST",path,json.dumps(payload),h)
        response=conn.getresponse()
        result=json.loads(response.read())
        conn.close()
        return response.status,result
    def test_gateway_pairs_and_queries_on_loopback(self):
        self.start_gateway()
        code=self.connector.create_pairing()["code"]
        status,p=self.request("/pair",{"code":code})
        self.assertEqual(status,200)
        status,p=self.request("/query",{"command":"search","query":"示例"},p["device_token"])
        self.assertEqual(status,200)
        self.assertEqual(p["data"]["messages"][0]["content"],"示例内容")
        self.assertFalse(p["coverage"]["complete"])
    def test_gateway_rejects_missing_auth_and_cross_origin(self):
        self.start_gateway()
        status,_=self.request("/query",{"command":"search","query":"x"})
        self.assertEqual(status,401)
        status,_=self.request("/pair",{"code":"x"},headers={"Origin":"https://other.example"})
        self.assertEqual(status,403)
    def test_gateway_rejects_rebinding_host_and_oversized_body(self):
        self.start_gateway()
        status,_=self.request("/pair",{"code":"x"},headers={"Host":"evil.example"})
        self.assertEqual(status,403)
        status,_=self.request("/pair",{"code":"x"*20000})
        self.assertEqual(status,413)
    def test_disconnect_stops_accepting_queries(self):
        self.start_gateway()
        token=self.connector.pair(self.connector.create_pairing()["code"])["device_token"]
        self.connector.disconnect()
        status,_=self.request("/query",{"command":"search","query":"x"},token)
        self.assertEqual(status,401)

    def test_switching_accounts_revokes_existing_device(self):
        self.ready()
        token=self.connector.pair(self.connector.create_pairing()["code"])["device_token"]
        self.connector.use_existing(self.home/"different-account.json")
        self.assertTrue(self.connector.ready)
        self.assertFalse(self.connector.authorize(token))
    def test_safe_access_can_be_sanitized_twice(self):
        value=safe_access(safe_access({"state":"ready","live_database_read_ok":True}))
        self.assertTrue(value["can_query"])
    def test_stop_invalidates_pending_verification(self):
        entered,release=threading.Event(),threading.Event()
        original=self.backend.access
        self.backend.state="ready"
        def blocked(*args):
            entered.set()
            release.wait(3)
            return original(*args)
        self.backend.access=blocked
        failures=[]
        def verify():
            try: self.connector.inspect()
            except ConnectorError as exc: failures.append(exc.code)
        thread=threading.Thread(target=verify)
        thread.start()
        self.assertTrue(entered.wait(3))
        self.connector.disconnect()
        release.set()
        thread.join(3)
        self.assertFalse(self.connector.status()["can_query"])
        self.assertIn("operation_cancelled",failures)

class RuntimeIntegrationTests(unittest.TestCase):
    """Real worker/Reader path over generated SQLite data, no user account."""
    def setUp(self):
        path=Path(__file__).resolve().parents[2]/"rion-wechat-reader/tests/test_reader.py"
        spec=importlib.util.spec_from_file_location("synthetic_reader_fixture",path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.fixture=module.ReaderContractTest(methodName="runTest")
        self.fixture.setUp()
        gc.collect()  # SQLite context managers commit but do not close on Windows.
        import rion_wechat_reader as reader
        if sys.platform=="win32":
            self.assertTrue(reader.windows_private_acl(Path(self.fixture.temp.name),initialize=True))
            reader.protect_new_file(self.fixture.config)
            reader.protect_new_file(self.fixture.keys)
        else:
            self.fixture.config.chmod(0o600)
        self.home=Path(self.fixture.temp.name).resolve()/"connector"
        self.fixture.config=self.fixture.config.resolve()
        self.connector=Connector(self.home,RuntimeBackend())
    def tearDown(self):
        gc.collect()
        self.fixture.tearDown()
    def test_real_worker_ready_pair_and_read_only_query(self):
        before={path.name:path.read_bytes() for path in self.fixture.data_root.glob("*.db")}
        try:
            result=self.connector.use_existing(self.fixture.config)
        except ConnectorError as exc:
            self.fail("Synthetic worker failure: "+exc.code)
        self.assertEqual(result["state"],"ready")
        self.assertTrue(result["can_query"])
        token=self.connector.pair(self.connector.create_pairing()["code"])["device_token"]
        if os.environ.get("CONNECTOR_TEST_EXECUTABLE"):
            from connector_core import runtime_command
            probe=subprocess.run(runtime_command()+["--worker","reader","--config",str(self.fixture.config),
                "--strict-read-only","search","你好","--limit","3"],capture_output=True,text=True,encoding="utf-8",timeout=30)
            # These inputs are generated in setUp; output cannot contain a real account.
            self.assertEqual(probe.returncode,0,"Synthetic Reader probe: "+probe.stdout[:3000]+probe.stderr[:1000])
        response=self.connector.query(token,{"command":"search","query":"你好"})
        self.assertTrue(response["ok"],response)
        self.assertTrue(any("你好" in row["content"] for row in response["data"]["messages"]))
        serialized=json.dumps(response,ensure_ascii=False)
        self.assertNotIn(str(self.fixture.data_root),serialized)
        self.assertNotIn(str(self.fixture.keys),serialized)
        self.assertEqual(before,{path.name:path.read_bytes() for path in self.fixture.data_root.glob("*.db")})
    def test_real_worker_import_and_restart(self):
        root=Path(self.fixture.temp.name).resolve()/"encrypted"
        root.mkdir()
        key="22"*32
        for source in (self.fixture.session,self.fixture.contact,self.fixture.message):
            self.fixture.encrypt_fixture(source,root/source.name,key)
        material=Path(self.fixture.temp.name).resolve()/"access.json"
        material.write_text(json.dumps({"schema_version":2,"keys":{
            path.read_bytes()[:16].hex():key for path in root.glob("*.db")}}))
        import rion_wechat_reader as reader
        reader.protect_new_file(material)
        try:
            result=self.connector.preview(str(root),str(material))
        except ConnectorError as exc:
            self.fail("Synthetic worker failure: "+exc.code)
        self.assertEqual(result["state"],"ready_to_configure",result)
        result=self.connector.apply(str(root),str(material))
        self.assertTrue(result["can_query"],result)
        restored=Connector(self.home,RuntimeBackend())
        self.assertTrue(restored.inspect()["can_query"])
        token=restored.pair(restored.create_pairing()["code"])["device_token"]
        evidence=restored.query(token,{"command":"search","query":"你好"})
        self.assertTrue(evidence["ok"],evidence)
        self.assertNotIn(key,json.dumps(evidence))
        restored.revoke()
        self.assertFalse(restored.status()["paired"])

if __name__=="__main__":
    unittest.main()
