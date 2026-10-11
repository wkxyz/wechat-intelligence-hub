"""Host integration contracts use synthetic credentials and messages only."""
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

def native_command():
    executable=os.environ.get('CONNECTOR_TEST_EXECUTABLE')
    return [str(Path(executable).resolve())] if executable else [sys.executable,str(PROJECT/'connector_runtime.py')]

class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name).resolve() / 'host'
        self.addCleanup(self.temp.cleanup)
        import host_client
        self.module = host_client
        self.client = host_client.HostClient(self.home)

    def exchange(self, body, status=200):
        class Response:
            def __init__(self):
                self.status = status
                self.buffer = io.BytesIO(json.dumps(body).encode())
            def read(self, size):
                return self.buffer.read(size)
        patcher = patch.object(self.module.http.client, 'HTTPConnection')
        connection = patcher.start()
        self.addCleanup(patcher.stop)
        connection.return_value.getresponse.return_value = Response()
        return connection

    def bind(self):
        connection = self.exchange({'ok':True,'device_token':'synthetic-session','session':'expires_on_restart'})
        self.client.pair('synthetic-pair')
        return connection

    def test_unpaired_client_never_queries_network(self):
        connection = self.exchange({'ok':True})
        with self.assertRaises(self.module.HostError) as error:
            self.client.query({'command':'history','chat':'示例群'})
        self.assertEqual(error.exception.code, 'pairing_required')
        connection.assert_not_called()

    def test_pairing_saves_private_binding_without_printing_token(self):
        connection = self.bind()
        self.assertEqual(connection.call_args.args, ('127.0.0.1',17673))
        self.assertNotIn('synthetic-session', json.dumps(self.client.descriptor(['connector'])))
        if sys.platform != 'win32':
            self.assertEqual(stat.S_IMODE((self.home/'host-session.json').stat().st_mode),0o600)
        self.assertEqual(json.loads((self.home/'host-session.json').read_text())['device_token'],'synthetic-session')

    def test_query_validation_precedes_network_and_credentials(self):
        connection = self.exchange({'ok':True})
        for request in ({'command':'sql'}, {'command':'search','query':'x','db':'/private'},
                        {'command':'search','query':'x','live':False}):
            with self.assertRaises(self.module.HostError): self.client.query(request)
        connection.assert_not_called()

    def test_authorized_query_forces_live_and_uses_token_header_only(self):
        self.bind()
        connection = self.exchange({'ok':True,'data':{'messages':[{'content':'虚构消息'}]}})
        result = self.client.query({'command':'search','query':'虚构'})
        self.assertEqual(result['data']['messages'][0]['content'],'虚构消息')
        args, kwargs = connection.return_value.request.call_args
        body = json.loads(args[2])
        self.assertTrue(body['live'])
        self.assertNotIn(b'synthetic-session', args[2])
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer synthetic-session')

    def test_error_body_is_not_exposed_and_no_retry_occurs(self):
        self.bind()
        connection = self.exchange({'ok':False,'error':{'code':'unauthorized','message':'private raw secret'}},401)
        with self.assertRaises(self.module.HostError) as error: self.client.status()
        self.assertEqual(error.exception.code,'pairing_required')
        self.assertNotIn('private raw secret', str(error.exception))
        self.assertEqual(connection.return_value.request.call_count,1)

    def test_descriptor_uses_explicit_command_and_no_secret_environment(self):
        descriptor = self.client.descriptor(['native executable','--mcp'])
        entry = descriptor['mcpServers']['wechat']
        self.assertEqual(entry['command'],'native executable')
        self.assertEqual(entry['args'],['--mcp'])
        self.assertEqual(entry['env'],{'WECHAT_HOST_HOME':str(self.home)})
        self.assertNotIn('token',json.dumps(descriptor).lower())

    def test_private_binding_required_and_symlink_binding_rejected(self):
        self.bind()
        if sys.platform == 'win32': return
        path=self.home/'host-session.json'
        path.chmod(0o644)
        with self.assertRaises(self.module.HostError):self.client.status()
        path.chmod(0o600)
        target=self.home/'other.json'
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(self.module.HostError):self.client.status()

    def test_pairing_wrong_response_does_not_create_binding(self):
        self.exchange({'ok':True,'device_token':42})
        with self.assertRaises(self.module.HostError):self.client.pair('synthetic-pair')
        self.assertFalse((self.home/'host-session.json').exists())

    def test_export_writes_usable_configuration_without_credentials(self):
        self.bind()
        destination=Path(self.temp.name)/'wechat-mcp.json'
        self.client.export(destination,['native executable','--mcp'])
        value=json.loads(destination.read_text(encoding='utf-8'))
        self.assertEqual(value['mcpServers']['wechat']['args'],['--mcp'])
        self.assertNotIn('synthetic-session',destination.read_text())

class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env=dict(os.environ,WECHAT_HOST_HOME=self.temp.name,PYTHONDONTWRITEBYTECODE='1')

    def conversation(self, messages):
        data=''.join(json.dumps(m,ensure_ascii=False)+'\n' for m in messages)
        result=subprocess.run(native_command()+['--mcp'],
            input=data,capture_output=True,text=True,encoding='utf-8',env=self.env,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stderr,'')
        return [json.loads(line) for line in result.stdout.splitlines()]

    def start(self):
        return [{'jsonrpc':'2.0','id':1,'method':'initialize','params':{
            'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-host','version':'1'}}},
            {'jsonrpc':'2.0','method':'notifications/initialized'}]

    def test_real_stdio_negotiates_and_lists_only_read_only_tools(self):
        replies=self.conversation(self.start()+[{'jsonrpc':'2.0','id':2,'method':'tools/list'}])
        self.assertEqual(replies[0]['result']['protocolVersion'],'2025-11-25')
        tools=replies[1]['result']['tools']
        self.assertEqual([t['name'] for t in tools],['wechat_status','wechat_query'])
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in tools))
        self.assertNotIn('db',tools[1]['inputSchema']['properties'])

    def test_status_without_pairing_returns_tool_error_not_raw_files(self):
        replies=self.conversation(self.start()+[{'jsonrpc':'2.0','id':3,'method':'tools/call',
            'params':{'name':'wechat_status','arguments':{}}}])
        result=replies[-1]['result']
        self.assertTrue(result['isError'])
        self.assertEqual(json.loads(result['content'][0]['text'])['error']['code'],'pairing_required')
        self.assertNotIn(self.temp.name,json.dumps(result))

    def test_pre_initialization_and_unknown_method_return_protocol_errors(self):
        replies=self.conversation([{'jsonrpc':'2.0','id':0,'method':'tools/list'}]+self.start()+
            [{'jsonrpc':'2.0','id':4,'method':'shell/execute'}])
        self.assertEqual(replies[0]['error']['code'],-32002)
        self.assertEqual(replies[-1]['error']['code'],-32601)

    def test_status_rejects_arguments_and_query_rejects_unknown_tool_fields(self):
        messages=self.start()
        for i,params in enumerate(({'name':'wechat_status','arguments':{'db':'private'}},
                {'name':'wechat_query','arguments':{'command':'search','query':'x','reader':'private'}},
                {'name':'send_wechat','arguments':{}}),2):
            messages.append({'jsonrpc':'2.0','id':i,'method':'tools/call','params':params})
        replies=self.conversation(messages)
        self.assertEqual(replies[1]['error']['code'],-32602)
        self.assertTrue(replies[2]['result']['isError'])
        self.assertEqual(replies[3]['error']['code'],-32602)

    def test_host_query_mode_returns_json_without_printing_tracebacks(self):
        result=subprocess.run(native_command()+['--host-query'],
            input=json.dumps({'command':'search','query':'虚构'}),capture_output=True,text=True,encoding='utf-8',env=self.env,timeout=30)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout)['error']['code'],'pairing_required')
        self.assertEqual(result.stderr,'')

    def test_mcp_dispatches_real_tool_result_and_preserves_coverage(self):
        import host_mcp
        class Client:
            def query(self,request):
                return {'ok':True,'data':{'messages':[{'content':'虚构消息'}]},'coverage':{'complete':False}}
        server=host_mcp.MCPServer(Client())
        for message in self.start():server.handle(message)
        reply=server.handle({'jsonrpc':'2.0','id':7,'method':'tools/call',
            'params':{'name':'wechat_query','arguments':{'command':'search','query':'虚构'}}})
        self.assertFalse(reply['result'].get('isError',False))
        self.assertFalse(reply['result']['structuredContent']['coverage']['complete'])

    def test_invalid_rpc_envelope_and_array_params_are_rejected(self):
        replies=self.conversation([[],{'jsonrpc':'1.0','id':1,'method':'ping'},
            {'jsonrpc':'2.0','id':2,'method':'initialize','params':[]}])
        self.assertEqual([r['error']['code'] for r in replies],[-32600,-32600,-32602])

class HostFlowTests(unittest.TestCase):
    setUp=MCPTests.setUp
    conversation=MCPTests.conversation
    start=MCPTests.start
    # Exercise the real fixed loopback gateway and a separate native MCP process.
    # Generated messages only; this test does not read a user's account.
    def test_pair_stdio_query_then_revocation(self):
        from connector_core import Connector, Gateway
        from test_connector import FakeBackend
        from host_client import HostClient
        backend=FakeBackend()
        backend.state='ready'
        connector=Connector(Path(self.temp.name)/'connector',backend)
        connector.inspect()
        gateway=Gateway(connector)
        gateway.start()
        self.addCleanup(gateway.stop)
        client=HostClient(self.temp.name)
        code=connector.create_pairing()['code']
        self.assertTrue(client.pair(code)['paired'])
        request={'jsonrpc':'2.0','id':9,'method':'tools/call','params':{
            'name':'wechat_query','arguments':{'command':'search','query':'示例','limit':2}}}
        replies=self.conversation(self.start()+[request])
        value=replies[-1]['result']['structuredContent']
        self.assertEqual(value['data']['messages'][0]['content'],'示例内容')
        self.assertFalse(value['coverage']['complete'])
        connector.revoke()
        revoked=self.conversation(self.start()+[request])[-1]['result']
        self.assertTrue(revoked['isError'])
        self.assertEqual(revoked['structuredContent']['error']['code'],'pairing_required')


if __name__ == '__main__': unittest.main()
