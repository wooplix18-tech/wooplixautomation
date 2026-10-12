import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from app.config import load_environment
from app.storage import PostgresProjectStore, ProjectStore, StorageConflict
from app.workflow import WorkflowEngine


class EnvironmentTests(unittest.TestCase):
    def test_local_settings_load_from_project_directory_without_overriding_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.env.local').write_text('DATABASE_URL=local\nAI_MODEL=local-model\n')
            (root / '.env').write_text('DATABASE_URL=base\nGROQ_MODEL=base-model\n')
            with patch.dict(os.environ, {'AI_MODEL': 'shell-model'}, clear=True):
                load_environment(root)
                self.assertEqual(os.environ['DATABASE_URL'], 'local')
                self.assertEqual(os.environ['AI_MODEL'], 'shell-model')
                self.assertEqual(os.environ['GROQ_MODEL'], 'base-model')

    def test_vercel_never_loads_local_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.env.local').write_text('DATABASE_URL=wrong-account\n')
            with patch.dict(os.environ, {'VERCEL': '1'}, clear=True):
                load_environment(root)
                self.assertNotIn('DATABASE_URL', os.environ)

    def test_postgres_store_does_not_connect_during_import(self):
        with patch('psycopg.connect', side_effect=AssertionError('Unexpected build-time connection')):
            store = PostgresProjectStore('postgresql://placeholder.invalid/db')
        self.assertEqual(store.backend, 'neon_postgres')


class APITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        environment = patch.dict(os.environ, {
            'VERCEL': '1', 'DATABASE_URL': '', 'OPENAI_API_KEY': '', 'GROQ_API_KEY': '',
            'AUTOMATION_DB_PATH': str(Path(self.folder.name) / 'test.db'),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.main = importlib.import_module('app.main')
        self.store = ProjectStore(Path(self.folder.name) / 'test.db')
        module_patch = patch.multiple(self.main, store=self.store, engine=WorkflowEngine(self.store))
        module_patch.start()
        self.addCleanup(module_patch.stop)
        self.client = TestClient(self.main.app)
        self.addCleanup(self.client.close)
        self.payload = {'project_name': 'API smoke project', 'brief': 'Build a responsive customer portal with status tracking.'}

    def test_dashboard_health_and_metadata(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Tell us what you want to build', response.text)
        health = self.client.get('/api/health')
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()['durable_storage'])
        self.assertEqual(self.client.get('/api/agents').status_code, 200)
        self.assertEqual(self.client.get('/api/workflow').status_code, 200)

    def test_complete_api_lifecycle_and_persistence(self):
        response = self.client.post('/api/projects', json=self.payload)
        self.assertEqual(response.status_code, 201)
        project = response.json()
        for decision, status in [('approve', 'delivery_review'), ('approve', 'uat'), ('pass', 'completed')]:
            response = self.client.post(f"/api/projects/{project['id']}/gates/{project['active_gate_id']}/respond", json={'decision': decision})
            self.assertEqual(response.status_code, 200)
            project = response.json()
            self.assertEqual(project['status'], status)
        self.assertEqual(len(project['artifacts']), 7)
        restored = ProjectStore(self.store.path).get(project['id'])
        self.assertEqual(restored.status, 'completed')
        self.assertEqual(self.client.get(f"/api/projects/{project['id']}/artifacts/handover").status_code, 200)
        self.assertEqual(len(self.client.get('/api/projects').json()['projects']), 1)

    def test_invalid_input_and_missing_project(self):
        self.assertEqual(self.client.post('/api/projects', json={'project_name': 'x', 'brief': 'short'}).status_code, 422)
        self.assertEqual(self.client.get('/api/projects/missing').status_code, 404)
        self.assertEqual(self.client.post('/api/projects/missing/gates/missing/respond', json={'decision': 'approve'}).status_code, 404)

    def test_invalid_and_duplicate_gate_decisions(self):
        project = self.client.post('/api/projects', json=self.payload).json()
        endpoint = f"/api/projects/{project['id']}/gates/{project['active_gate_id']}/respond"
        self.assertEqual(self.client.post(endpoint, json={'decision': 'fail'}).status_code, 409)
        self.assertEqual(self.client.post(endpoint, json={'decision': 'approve'}).status_code, 200)
        self.assertEqual(self.client.post(endpoint, json={'decision': 'approve'}).status_code, 409)

    def test_database_failure_is_reported_without_credentials(self):
        with patch.object(self.store, 'check_connection', side_effect=psycopg.OperationalError('secret-password')):
            response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret-password', response.text)

    def test_stale_save_returns_conflict(self):
        with patch.object(self.store, 'save', side_effect=StorageConflict('Stale state')):
            response = self.client.post('/api/projects', json=self.payload)
        self.assertEqual(response.status_code, 409)

    def test_chat_to_automatic_delivery_api(self):
        response = self.client.post('/api/intake/chat', json={'messages': [{'role': 'user', 'content': 'Build a website with booking, payments and notifications.'}]})
        self.assertEqual(response.status_code, 200)
        draft = response.json()['draft']
        response = self.client.post('/api/projects', json=draft)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['status'], 'pack_ready')
        self.assertEqual(len(response.json()['artifacts']), 7)

    def test_document_upload_and_rejection(self):
        text = 'Build a support portal with email notifications and status tracking.'
        response = self.client.post('/api/intake/document', files={'file': ('requirements.txt', text.encode(), 'text/plain')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['text'], text)
        response = self.client.post('/api/intake/document', files={'file': ('file.exe', b'not a document', 'application/octet-stream')})
        self.assertEqual(response.status_code, 415)


if __name__ == '__main__':
    unittest.main()
