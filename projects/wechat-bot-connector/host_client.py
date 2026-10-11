"""Native host adapter. Account keys stay in Reader; destinations are fixed."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path

from connector_core import ConnectorError, DEFAULT_PORT, query_args

MAX_RESPONSE = 1_048_576


class HostError(Exception):
    def __init__(self, code, message='连接未完成，请检查本机连接器并重新配对。'):
        self.code = code
        super().__init__(message)


class HostClient:
    def __init__(self, home=None):
        self.home = Path(home or os.environ.get('WECHAT_HOST_HOME',
            str(Path.home()/'.config/wechat-bot-connector'))).expanduser().resolve()
        self.binding = self.home/'host-session.json'

    def _post(self, path, payload, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', DEFAULT_PORT, timeout=35)
        headers = {'Content-Type':'application/json'}
        if token is not None:
            headers['Authorization'] = 'Bearer '+token
        try:
            body = json.dumps(payload,ensure_ascii=False)
            connection.request('POST',path,body.encode('utf-8'),headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE+1)
            if len(raw)>MAX_RESPONSE:
                raise HostError('response_too_large')
            value = json.loads(raw)
            if not isinstance(value,dict):
                raise HostError('invalid_response')
            if response.status in (401,403):
                raise HostError('pairing_required')
            if response.status!=200 or value.get('ok') is not True:
                code = value.get('error',{}).get('code') if isinstance(value.get('error'),dict) else None
                known = {'busy','rate_limited','not_ready','reader_timeout','reader_failed',
                         'ambiguous_chat','chat_not_found','invalid_request'}
                raise HostError(code if code in known else 'connector_failed')
            return value
        except HostError:
            raise
        except (OSError,ValueError,UnicodeError,RecursionError,http.client.HTTPException):
            raise HostError('connector_unavailable') from None
        finally:
            connection.close()

    def _token(self):
        import rion_wechat_reader as reader
        try:
            if self.binding.is_symlink() or not reader.safe_mode(self.binding):
                raise HostError('private_binding_required')
            if self.binding.stat().st_size>4096:
                raise HostError('private_binding_required')
            value=json.loads(self.binding.read_text(encoding='utf-8'))
            token=value.get('device_token') if isinstance(value,dict) else None
            if not isinstance(token,str) or not 1<=len(token)<=128 or not token.isascii() or any(c.isspace() for c in token):
                raise HostError('pairing_required')
            return token
        except FileNotFoundError:
            raise HostError('pairing_required') from None
        except (OSError,ValueError,UnicodeError,RecursionError):
            raise HostError('private_binding_required') from None

    def pair(self, code):
        if not isinstance(code,str) or not 1<=len(code)<=128:
            raise HostError('invalid_request')
        value=self._post('/pair',{'code':code})
        token=value.get('device_token')
        if not isinstance(token,str) or not 1<=len(token)<=128 or not token.isascii() or any(c.isspace() for c in token):
            raise HostError('invalid_response')
        import rion_wechat_reader as reader
        try:
            reader.secure_write_json(self.binding,{'schema_version':1,'device_token':token,
                'session':'expires_on_connector_restart'},force=True)
        except (OSError,reader.ReaderError):
            raise HostError('private_storage_failed') from None
        return {'ok':True,'paired':True,'session':'expires_on_connector_restart'}

    def status(self):
        return self._post('/status',{},self._token())

    def query(self, request):
        try:
            query_args(request)
        except ConnectorError:
            raise HostError('invalid_request','只允许有界的检索、主题、历史和回复证据查询。') from None
        return self._post('/query',{**request,'live':True},self._token())

    def descriptor(self, command):
        if not isinstance(command,list) or not command or not all(isinstance(x,str) and x for x in command):
            raise HostError('invalid_request')
        return {'mcpServers':{'wechat':{'command':command[0],'args':command[1:],
            'env':{'WECHAT_HOST_HOME':str(self.home)}}}}

    def export(self, destination, command):
        value=self.descriptor(command)
        try:
            Path(destination).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        except OSError:
            raise HostError('configuration_export_failed') from None
