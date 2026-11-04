import json
import unittest
from unittest.mock import patch, MagicMock
import live_meeting_app as app


class LocalLlmCpuTests(unittest.TestCase):
    def connection(self, result):
        conn = MagicMock()
        conn.getresponse.return_value.status = 200
        conn.getresponse.return_value.read.return_value = json.dumps({'message': {'content': json.dumps(result)}}).encode()
        return conn

    def test_live_json_uses_non_thinking_and_cpu_suitable_timeout(self):
        boxes = app.default_boxes()[:1]
        result = {'summary': 'Reunião de teste.', 'results': [{'id': boxes[0]['id'], 'text': 'Nenhuma pergunta.'}]}
        conn = self.connection(result)
        with patch.object(app.http.client, 'HTTPConnection', return_value=conn) as factory:
            app._ollama_call(11435, 'gemma4:e4b', 'Reunião de teste.', boxes)
        payload = json.loads(conn.request.call_args.kwargs['body'])
        self.assertIs(payload.get('think'), False, 'Gemma thinking must be disabled for bounded structured analysis')
        self.assertGreaterEqual(factory.call_args.kwargs['timeout'], 600)
        self.assertFalse(payload['stream'])
        self.assertEqual(factory.call_args.args, ('127.0.0.1', 11435))

    def test_final_json_uses_non_thinking_and_cpu_suitable_timeout(self):
        conn = self.connection({'notes': 'Teste'})
        with patch.object(app.http.client, 'HTTPConnection', return_value=conn) as factory:
            app._ollama_json_call(11435, 'gemma4:e4b', 'Sistema', 'Teste', {'type': 'object'})
        payload = json.loads(conn.request.call_args.kwargs['body'])
        self.assertIs(payload.get('think'), False)
        self.assertGreaterEqual(factory.call_args.kwargs['timeout'], 600)
        self.assertFalse(payload['stream'])
        self.assertEqual(factory.call_args.args, ('127.0.0.1', 11435))
