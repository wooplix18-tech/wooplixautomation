import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from app.agents import AIClient


class ProviderTests(unittest.TestCase):
    def test_groq_sdk_preserves_json_and_uses_correct_endpoint_base(self):
        with patch.dict('os.environ', {'GROQ_API_KEY':'synthetic-test-key', 'GROQ_MODEL':'openai/gpt-oss-120b', 'GROQ_BASE_URL':'https://api.groq.com/openai/v1'}):
            provider = AIClient()
        sdk = MagicMock()
        sdk.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'ok':True})))])
        with patch('app.agents.Groq') as factory:
            factory.return_value.__enter__.return_value = sdk
            self.assertEqual(provider.json('Return JSON.','Synthetic test.',{}), {'ok':True})
        self.assertEqual(factory.call_args.kwargs['base_url'], 'https://api.groq.com')
        self.assertEqual(sdk.chat.completions.create.call_args.kwargs['reasoning_effort'], 'low')

    def test_malformed_model_json_preserves_fallback(self):
        with patch.dict('os.environ', {'GROQ_API_KEY':'synthetic-test-key'}):
            provider = AIClient()
        sdk = MagicMock()
        sdk.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='not JSON'))])
        with patch('app.agents.Groq') as factory:
            factory.return_value.__enter__.return_value = sdk
            self.assertEqual(provider.json('Return JSON.','Synthetic test.',{'fallback':True}), {'fallback':True})
