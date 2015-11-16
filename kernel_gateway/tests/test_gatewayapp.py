# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.

import logging
import unittest
import os
import sys

from kernel_gateway.gatewayapp import KernelGatewayApp, ioloop
from jupyter_client.kernelspec import NoSuchKernel

from tornado.gen import coroutine, Return
from tornado.websocket import websocket_connect
from tornado.httpclient import HTTPRequest
from tornado.testing import gen_test
from tornado.testing import AsyncHTTPTestCase, LogTrapTestCase
from tornado.escape import json_encode, json_decode, url_escape

RESOURCES = os.path.join(os.path.dirname(__file__), 'resources')

class TestGatewayAppConfig(unittest.TestCase):
    def setUp(self):
        self.environ = dict(os.environ)

    def tearDown(self):
        os.environ = self.environ

    def test_config_env_vars(self):
        '''Env vars should be honored for traitlets.'''
        # Environment vars are always strings
        os.environ['KG_PORT'] = '1234'
        os.environ['KG_IP'] = '1.1.1.1'
        os.environ['KG_AUTH_TOKEN'] = 'fake-token'
        os.environ['KG_ALLOW_CREDENTIALS'] = 'true'
        os.environ['KG_ALLOW_HEADERS'] = 'Authorization'
        os.environ['KG_ALLOW_METHODS'] = 'GET'
        os.environ['KG_ALLOW_ORIGIN'] = '*'
        os.environ['KG_EXPOSE_HEADERS'] = 'X-Fake-Header'
        os.environ['KG_MAX_AGE'] = '5'
        os.environ['KG_BASE_URL'] = '/fake/path'
        os.environ['KG_MAX_KERNELS'] = '1'

        app = KernelGatewayApp()
        
        self.assertEqual(app.port, 1234)
        self.assertEqual(app.ip, '1.1.1.1')
        self.assertEqual(app.auth_token, 'fake-token')
        self.assertEqual(app.allow_credentials, 'true')
        self.assertEqual(app.allow_headers, 'Authorization')
        self.assertEqual(app.allow_methods, 'GET')
        self.assertEqual(app.allow_origin, '*')
        self.assertEqual(app.expose_headers, 'X-Fake-Header')
        self.assertEqual(app.max_age, '5')
        self.assertEqual(app.base_url, '/fake/path')
        self.assertEqual(app.max_kernels, 1)

class TestGatewayAppBase(AsyncHTTPTestCase, LogTrapTestCase):
    def get_new_ioloop(self):
        '''Use a global zmq ioloop for tests.'''
        return ioloop.IOLoop.current()

    def get_app(self):
        '''Returns a tornado.web.Application for system tests.'''
        if hasattr(self, '_app'):
            return self._app
        self.app = KernelGatewayApp(log_level=logging.CRITICAL)
        self.setup_app()
        self.app.init_configurables()
        self.app.init_webapp()
        return self.app.web_app

    def setup_app(self):
        '''
        Override to configure KernelGatewayApp instance before initializing
        configurables and the web app.
        '''
        pass

    @coroutine
    def spawn_kernel(self):
        '''
        Code to spawn a kernel and return a websocket connection to it.
        '''
        # Request a kernel
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='POST',
            body='{}'
        )
        self.assertEqual(response.code, 201)

        # Connect to the kernel via websocket
        kernel = json_decode(response.body)
        ws_url = 'ws://localhost:{}/api/kernels/{}/channels'.format(
            self.get_http_port(),
            url_escape(kernel['id'])
        )

        ws = yield websocket_connect(ws_url)
        raise Return(ws)

class TestGatewayApp(TestGatewayAppBase):
    @gen_test
    def test_startup(self):
        '''Root of kernels resource should be OK.'''
        response = yield self.http_client.fetch(self.get_url('/api/kernels'))
        self.assertEqual(response.code, 200)

    @gen_test
    def test_headless(self):
        '''Other notebook resources should not exist.'''
        response = yield self.http_client.fetch(self.get_url('/api/contents'),
            raise_error=False)
        self.assertEqual(response.code, 404)
        response = yield self.http_client.fetch(self.get_url('/'),
            raise_error=False)
        self.assertEqual(response.code, 404)
        resp = yield self.http_client.fetch(self.get_url('/tree'),
            raise_error=False)
        self.assertEqual(response.code, 404)

    @gen_test
    def test_auth_token(self):
        '''All server endpoints should check the configured auth token.'''
        # Set token requirement
        app = self.get_app()
        app.settings['kg_auth_token'] = 'fake-token'

        # Requst API without the token
        response = yield self.http_client.fetch(
            self.get_url('/api'),
            method='GET',
            raise_error=False
        )
        self.assertEqual(response.code, 401)

        # Now with it
        response = yield self.http_client.fetch(
            self.get_url('/api'),
            method='GET',
            headers={'Authorization': 'token fake-token'},
            raise_error=False
        )
        self.assertEqual(response.code, 200)

        # Requst kernelspecs without the token
        response = yield self.http_client.fetch(
            self.get_url('/api/kernelspecs'),
            method='GET',
            raise_error=False
        )
        self.assertEqual(response.code, 401)

        # Now with it
        response = yield self.http_client.fetch(
            self.get_url('/api/kernelspecs'),
            method='GET',
            headers={'Authorization': 'token fake-token'},
            raise_error=False
        )
        self.assertEqual(response.code, 200)

        # Request a kernel without the token
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='POST',
            body='{}',
            raise_error=False
        )
        self.assertEqual(response.code, 401)

        # Request with the token now
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='POST',
            body='{}',
            headers={'Authorization': 'token fake-token'},
            raise_error=False
        )
        self.assertEqual(response.code, 201)

        kernel = json_decode(response.body)
        # Request kernel info without the token
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels/'+url_escape(kernel['id'])),
            method='GET',
            raise_error=False
        )
        self.assertEqual(response.code, 401)

        # Now with it
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels/'+url_escape(kernel['id'])),
            method='GET',
            headers={'Authorization': 'token fake-token'},
            raise_error=False
        )
        self.assertEqual(response.code, 200)

        # Request websocket connection without the token
        ws_url = 'ws://localhost:{}/api/kernels/{}/channels'.format(
            self.get_http_port(),
            url_escape(kernel['id'])
        )
        # No option to ignore errors so try/except
        try:
            ws = yield websocket_connect(ws_url)
        except Exception as ex:
            self.assertEqual(ex.code, 401)
        else:
            self.assert_(False, 'no exception raised')

        # Now request the websocket with the token
        ws_req = HTTPRequest(ws_url, 
            headers={'Authorization': 'token fake-token'}
        )
        ws = yield websocket_connect(ws_req)
        ws.close()

    @gen_test
    def test_cors_headers(self):
        '''All kernel endpoints should respond with configured CORS headers.'''
        app = self.get_app()
        app.settings['kg_allow_credentials'] = 'false'
        app.settings['kg_allow_headers'] = 'Authorization,Content-Type'
        app.settings['kg_allow_methods'] = 'GET,POST'
        app.settings['kg_allow_origin'] = 'https://jupyter.org'
        app.settings['kg_expose_headers'] = 'X-My-Fake-Header'
        app.settings['kg_max_age'] = '600'

        # Get kernels to check headers
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='GET'
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(response.headers['Access-Control-Allow-Credentials'], 'false')
        self.assertEqual(response.headers['Access-Control-Allow-Headers'], 'Authorization,Content-Type')
        self.assertEqual(response.headers['Access-Control-Allow-Methods'], 'GET,POST')
        self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'https://jupyter.org')
        self.assertEqual(response.headers['Access-Control-Expose-Headers'], 'X-My-Fake-Header')
        self.assertEqual(response.headers['Access-Control-Max-Age'], '600')
        self.assertEqual(response.headers.get('Content-Security-Policy'), None)

    @gen_test
    def test_max_kernels(self):
        '''Number of kernels should be limited.'''
        app = self.get_app()
        app.settings['kg_max_kernels'] = 1

        # Request a kernel
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='POST',
            body='{}'
        )
        self.assertEqual(response.code, 201)

        # Request another
        response2 = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='POST',
            body='{}',
            raise_error=False
        )
        self.assertEqual(response2.code, 402)

        # Shut down the kernel
        kernel = json_decode(response.body)
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels/'+url_escape(kernel['id'])),
            method='DELETE'
        )
        self.assertEqual(response.code, 204) 

        # Try again
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='POST',
            body='{}'
        )
        self.assertEqual(response.code, 201)

    @gen_test
    def test_get_api(self):
        '''Server should respond with the API version metadata.'''
        response = yield self.http_client.fetch(
            self.get_url('/api')
        )
        self.assertEqual(response.code, 200)
        info = json_decode(response.body)
        self.assertIn('version', info)

    @gen_test
    def test_get_kernelspecs(self):
        '''Server should respond with kernel spec metadata.'''
        response = yield self.http_client.fetch(
            self.get_url('/api/kernelspecs')
        )
        self.assertEqual(response.code, 200)
        specs = json_decode(response.body)
        self.assertIn('kernelspecs', specs)
        self.assertIn('default', specs)

    @gen_test
    def test_get_kernels(self):
        '''Server should respond with running kernel information.'''
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels')
        )
        self.assertEqual(response.code, 200)
        kernels = json_decode(response.body)
        self.assertEqual(len(kernels), 0)

        # Launch a kernel
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='POST',
            body='{}'
        )
        self.assertEqual(response.code, 201)
        kernel = json_decode(response.body)

        # Check the list again
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels')
        )
        self.assertEqual(response.code, 200)
        kernels = json_decode(response.body)
        self.assertEqual(len(kernels), 1)
        self.assertEqual(kernels[0]['id'], kernel['id'])

    @gen_test
    def test_kernel_comm(self):
        '''Default kernel should launch and accept commands.'''
        ws = yield self.spawn_kernel()

        # Send a request for kernel info
        ws.write_message(json_encode({
            'header': {
                'username': '',
                'version': '5.0',
                'session': '',
                'msg_id': 'fake-msg-id',
                'msg_type': 'kernel_info_request'
            },
            'parent_header': {},
            'channel': 'shell',
            'content': {},
            'metadata': {},
            'buffers': {}
        }))

        # Assert the reply comes back. Test will timeout if this hangs.
        for _ in range(3):
            msg = yield ws.read_message()
            msg = json_decode(msg)
            if(msg['msg_type'] == 'kernel_info_reply'):
                break
        else:
            self.assert_(False, 'never received kernel_info_reply')
        ws.close()

class TestRelocatedGatewayApp(TestGatewayAppBase):
    def setup_app(self):
        self.app.base_url = '/fake/path'

    @gen_test
    def test_base_url(self):
        '''Server should mount resources under configured base.'''
        # Should not exist at root
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='GET',
            raise_error=False
        )
        self.assertEqual(response.code, 404)

        # Should exist under path
        response = yield self.http_client.fetch(
            self.get_url('/fake/path/api/kernels'),
            method='GET'
        )
        self.assertEqual(response.code, 200)

class TestSeedGatewayApp(TestGatewayAppBase):
    def setup_app(self):
        self.app.seed_uri = os.path.join(RESOURCES, 
            'zen{}.ipynb'.format(sys.version_info.major))

    @gen_test
    def test_seed(self):
        '''Kernel should have variables preseeded from notebook.'''
        ws = yield self.spawn_kernel()

        # Print the encoded "zen of python" string, the kernel should have
        # it imported
        ws.write_message(json_encode({
            'header': {
                'username': '',
                'version': '5.0',
                'session': '',
                'msg_id': 'fake-msg-id',
                'msg_type': 'execute_request'
            },
            'parent_header': {},
            'channel': 'shell',
            'content': {
                'code': 'print(this.s)',
                'silent': False,
                'store_history': False,
                'user_expressions' : {}
            },
            'metadata': {},
            'buffers': {}
        }))

        # Read messages until we see the output from the print or hit the 
        # test timeout
        while 1:
            msg = yield ws.read_message()
            msg = json_decode(msg)
            msg_type = msg['msg_type']
            parent_msg_id = msg['parent_header']['msg_id']
            if msg_type == 'stream' and parent_msg_id == 'fake-msg-id':
                content = msg['content']
                self.assertEqual(content['name'], 'stdout')
                self.assertIn('Gur Mra bs Clguba', content['text'])
                break

        ws.close()

class TestRemoteSeedGatewayApp(TestSeedGatewayApp):
    def setup_app(self):
        self.app.seed_uri = 'https://gist.githubusercontent.com/parente/ccd36bd7db2f617d58ce/raw/zen{}.ipynb'.format(sys.version_info.major)

class TestBadSeedGatewayApp(TestGatewayAppBase):
    def setup_app(self):
        self.app.seed_uri = os.path.join(RESOURCES, 
            'failing_code{}.ipynb'.format(sys.version_info.major))

    @gen_test
    def test_seed_error(self):
        '''
        Server should shutdown kernel and respond with error when seed notebook
        has an execution error.
        '''
        # Request a kernel
        response = yield self.http_client.fetch(
            self.get_url('/api/kernels'),
            method='POST',
            body='{}',
            raise_error=False
        )
        self.assertEqual(response.code, 500)

class TestMissingSeedKernelGatewayApp(unittest.TestCase):
    def test_seed_kernel_not_available(self):
        '''
        Server should error because seed notebook requires a kernel that is not
        installed.
        '''
        app = KernelGatewayApp()
        app.seed_uri = os.path.join(RESOURCES, 'unknown_kernel.ipynb')
        self.assertRaises(NoSuchKernel, app.init_configurables)

