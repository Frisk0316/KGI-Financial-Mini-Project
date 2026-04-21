import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app as app_module
import database
import llm


def build_modules(
    title='Module A',
    content='Useful financial training content.',
    domains=None,
    reading_time_minutes=2,
):
    return {
        'document_summary': 'A concise summary.',
        'domains': domains or ['CRM'],
        'total_modules': 1,
        'modules': [
            {
                'sequence_order': 1,
                'title': title,
                'content': content,
                'key_takeaway': 'Remember the main idea.',
                'reading_time_minutes': reading_time_minutes,
            }
        ],
    }


class KnowledgeShredderAppTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = database.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        database.DB_PATH = os.path.join(self.temp_dir.name, 'test.db')
        database.init_db()
        app_module.app.config['TESTING'] = True
        app_module.app.config['INLINE_GENERATION_JOBS'] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.client = None
        database.DB_PATH = self.original_db_path
        app_module.app.config['INLINE_GENERATION_JOBS'] = False
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass

    def api_headers(self, trainer_id='trainer_001'):
        return {'X-Trainer-Id': trainer_id}

    def test_domains_include_other_tag(self):
        domains = database.get_all_domains()
        self.assertIn('Other', {domain['domain_name'] for domain in domains})

    def test_generate_creates_completed_job_and_deduplicates_domains(self):
        doc_id = database.insert_document('trainer_001', 'sample.txt', 'A' * 300)

        with patch.object(
            app_module,
            'generate_micro_modules',
            return_value=build_modules(title='Updated Module', domains=['LifeInsurance', 'InvestmentLinked']),
        ):
            response = self.client.post(
                '/api/generate',
                json={'doc_id': doc_id, 'domain_ids': [1, 2, 2], 'trainer_id': 'trainer_001'},
                headers=self.api_headers(),
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'completed')

        job_response = self.client.get(f"/api/jobs/{payload['job_id']}", headers=self.api_headers())
        self.assertEqual(job_response.status_code, 200)
        job_payload = job_response.get_json()
        self.assertEqual(job_payload['status'], 'completed')
        self.assertEqual(job_payload['requested_domain_ids'], [1, 2])

        saved_doc = database.get_document_with_modules(doc_id, trainer_id='trainer_001')
        self.assertEqual({domain['domain_id'] for domain in saved_doc['domains']}, {1, 2})
        self.assertEqual(len(saved_doc['modules']), 1)
        self.assertEqual(saved_doc['modules'][0]['module_title'], 'Updated Module')

    def test_generate_multiple_documents_creates_one_job_per_document(self):
        doc_one = database.insert_document('trainer_001', 'first.txt', 'A' * 300)
        doc_two = database.insert_document('trainer_001', 'second.txt', 'B' * 300)

        with patch.object(
            app_module,
            'generate_micro_modules',
            side_effect=[
                build_modules(title='First Module', domains=['CRM', 'Compliance']),
                build_modules(title='Second Module', domains=['CRM', 'Compliance']),
            ],
        ):
            response = self.client.post(
                '/api/generate',
                json={'doc_ids': [doc_one, doc_two], 'domain_ids': [3, 4], 'trainer_id': 'trainer_001'},
                headers=self.api_headers(),
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload['total_jobs'], 2)
        self.assertEqual([job['doc_id'] for job in payload['jobs']], [doc_one, doc_two])

        first_doc = database.get_document_with_modules(doc_one, trainer_id='trainer_001')
        second_doc = database.get_document_with_modules(doc_two, trainer_id='trainer_001')
        self.assertEqual({domain['domain_id'] for domain in first_doc['domains']}, {3, 4})
        self.assertEqual({domain['domain_id'] for domain in second_doc['domains']}, {3, 4})
        self.assertEqual(first_doc['modules'][0]['module_title'], 'First Module')
        self.assertEqual(second_doc['modules'][0]['module_title'], 'Second Module')

    def test_generate_persists_custom_prompt_in_job_payload(self):
        doc_id = database.insert_document('trainer_001', 'sample.txt', 'A' * 300)

        with patch.object(
            app_module,
            'generate_micro_modules',
            return_value=build_modules(title='Prompted Module', domains=['CRM']),
        ):
            response = self.client.post(
                '/api/generate',
                json={
                    'doc_ids': [doc_id],
                    'domain_ids': [3],
                    'trainer_id': 'trainer_001',
                    'custom_prompt': 'Focus on client-friendly tone.',
                },
                headers=self.api_headers(),
            )

        self.assertEqual(response.status_code, 202)
        job_payload = self.client.get(
            f"/api/jobs/{response.get_json()['job_id']}",
            headers=self.api_headers(),
        ).get_json()
        self.assertEqual(job_payload['requested_custom_prompt'], 'Focus on client-friendly tone.')

    def test_generate_failed_job_preserves_existing_data(self):
        doc_id = database.insert_document('trainer_001', 'sample.txt', 'B' * 300)
        database.save_generated_content(doc_id, [2], build_modules(title='Stable Module')['modules'])

        with patch.object(app_module, 'generate_micro_modules', side_effect=ValueError('missing modules')):
            response = self.client.post(
                '/api/generate',
                json={'doc_id': doc_id, 'domain_ids': [3], 'trainer_id': 'trainer_001'},
                headers=self.api_headers(),
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'failed')

        job_payload = self.client.get(f"/api/jobs/{payload['job_id']}", headers=self.api_headers()).get_json()
        self.assertIn('invalid module data', job_payload['error_message'])

        saved_doc = database.get_document_with_modules(doc_id, trainer_id='trainer_001')
        self.assertEqual([domain['domain_id'] for domain in saved_doc['domains']], [2])
        self.assertEqual(saved_doc['modules'][0]['module_title'], 'Stable Module')

    def test_upload_returns_redacted_preview(self):
        source = '聯絡窗口 test@example.com，電話 0912-345-678，身份證字號 A123456789。'
        response = self.client.post(
            '/api/upload',
            data={'file': (io.BytesIO(source.encode('utf-8')), 'sensitive.txt'), 'trainer_id': 'trainer_red'},
            content_type='multipart/form-data',
            headers=self.api_headers('trainer_red'),
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload['trainer_id'], 'trainer_red')
        self.assertIn('[REDACTED_EMAIL]', payload['preview_text'])
        self.assertIn('[REDACTED_PHONE]', payload['preview_text'])
        self.assertIn('[REDACTED_TW_ID]', payload['preview_text'])

    def test_document_route_enforces_trainer_ownership(self):
        doc_id = database.insert_document('trainer_owner', 'sample.txt', 'C' * 300)

        owner_response = self.client.get(f'/api/document/{doc_id}', headers=self.api_headers('trainer_owner'))
        self.assertEqual(owner_response.status_code, 200)
        owner_payload = owner_response.get_json()
        self.assertNotIn('raw_text', owner_payload)
        self.assertIn('preview_text', owner_payload)

        other_response = self.client.get(f'/api/document/{doc_id}', headers=self.api_headers('trainer_other'))
        self.assertEqual(other_response.status_code, 404)

    def test_generate_requires_integer_domain_ids(self):
        doc_id = database.insert_document('trainer_001', 'sample.txt', 'D' * 300)

        response = self.client.post(
            '/api/generate',
            json={'doc_id': doc_id, 'domain_ids': ['bad-id'], 'trainer_id': 'trainer_001'},
            headers=self.api_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('valid integers', response.get_json()['error'])

    def test_generate_rejects_invalid_doc_ids(self):
        response = self.client.post(
            '/api/generate',
            json={'doc_ids': ['bad-id'], 'domain_ids': [1], 'trainer_id': 'trainer_001'},
            headers=self.api_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('doc_ids', response.get_json()['error'])

    def test_generate_returns_failed_job_when_api_key_missing(self):
        doc_id = database.insert_document('trainer_001', 'training.txt', 'E' * 300)

        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post(
                '/api/generate',
                json={'doc_id': doc_id, 'domain_ids': [3], 'trainer_id': 'trainer_001'},
                headers=self.api_headers(),
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()['status'], 'failed')

        job_payload = self.client.get(
            f"/api/jobs/{response.get_json()['job_id']}",
            headers=self.api_headers(),
        ).get_json()
        self.assertIn('OPENAI_API_KEY', job_payload['error_message'])

    def test_generate_rejects_invalid_trainer_id(self):
        response = self.client.post(
            '/api/upload',
            data={'file': (io.BytesIO(b'X' * 80), 'sample.txt'), 'trainer_id': 'bad id'},
            content_type='multipart/form-data',
            headers=self.api_headers('bad id'),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('trainer_id', response.get_json()['error'])

    def test_upload_accepts_markdown_files(self):
        source = '# Sample\n\nThis markdown file contains enough content to pass the parser threshold for upload.'
        response = self.client.post(
            '/api/upload',
            data={'file': (io.BytesIO(source.encode('utf-8')), 'notes.md'), 'trainer_id': 'trainer_md'},
            content_type='multipart/form-data',
            headers=self.api_headers('trainer_md'),
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload['file_name'], 'notes.md')

    def test_generate_micro_modules_returns_structured_output(self):
        mock_payload = {
            'document_summary': 'A concise summary.',
            'domains': ['WealthManagement', 'TaxRegulations'],
            'total_modules': 1,
            'modules': [
                {
                    'sequence_order': 1,
                    'title': 'Estate Planning Essentials',
                    'content': 'Review beneficiary needs, tax exposure, and follow-up actions.',
                    'key_takeaway': 'Align wealth and tax planning with client goals.',
                    'reading_time_minutes': 2.7,
                }
            ],
        }

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}, clear=True):
            with patch.object(llm, '_request_structured_output', return_value=mock_payload) as request_mock:
                result = llm.generate_micro_modules(
                    'Estate planning often spans tax, insurance, and wealth preservation decisions.',
                    ['WealthManagement', 'TaxRegulations'],
                    custom_prompt='Highlight client communication priorities.',
                )

        self.assertEqual(result['total_modules'], len(result['modules']))
        self.assertEqual(result['domains'], ['WealthManagement', 'TaxRegulations'])
        self.assertEqual(result['modules'][0]['reading_time_minutes'], llm.TARGET_READING_TIME_MINUTES)
        self.assertEqual(request_mock.call_args.args[1], llm.DEFAULT_MODEL)
        self.assertIn('domains: WealthManagement, TaxRegulations', request_mock.call_args.args[2])
        self.assertIn('Highlight client communication priorities.', request_mock.call_args.args[2])

    def test_generate_micro_modules_rejects_domain_mismatch(self):
        mismatched_payload = {
            'document_summary': 'A concise summary.',
            'domains': ['CRM'],
            'total_modules': 1,
            'modules': [
                {
                    'sequence_order': 1,
                    'title': 'Client Service Basics',
                    'content': 'Follow the documented relationship steps.',
                    'key_takeaway': 'Stay consistent.',
                    'reading_time_minutes': 2,
                }
            ],
        }

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}, clear=True):
            with patch.object(llm, '_request_structured_output', return_value=mismatched_payload):
                with self.assertRaises(ValueError):
                    llm.generate_micro_modules('Sample text', ['Compliance'])

    def test_generate_micro_modules_supports_mock_mode_without_api_key(self):
        with patch.dict(os.environ, {'MOCK_LLM': 'true'}, clear=True):
            result = llm.generate_micro_modules(
                'This document explains treasury operations, customer servicing workflows, and control checkpoints.',
                ['CRM', 'Compliance'],
                custom_prompt='Keep the tone practical.',
            )

        self.assertEqual(result['domains'], ['CRM', 'Compliance'])
        self.assertGreaterEqual(len(result['modules']), 1)
        self.assertIn('Keep the tone practical.', result['document_summary'])
        self.assertNotEqual(result['modules'][0]['title'], 'Sprint 1')
        self.assertIn('Domains applied', result['modules'][0]['key_takeaway'])
        self.assertIn('Application:', result['modules'][0]['content'])
        self.assertIn('Keep the tone practical.', result['modules'][0]['content'])

    def test_generate_micro_modules_returns_readable_chinese_mock_output(self):
        with patch.dict(os.environ, {'MOCK_LLM': 'true'}, clear=True):
            result = llm.generate_micro_modules(
                '本文件說明客戶服務流程、法遵查核節點與保單受理注意事項，提供第一線同仁作業參考。',
                ['CRM', 'Compliance'],
                custom_prompt='請用實務口吻整理重點。',
            )

        self.assertIn('請用實務口吻整理重點。', result['document_summary'])
        self.assertTrue(
            any(
                phrase in result['modules'][0]['title']
                for phrase in ('客戶服務與溝通重點', '法遵要求與作業重點')
            )
        )
        self.assertIn('套用領域', result['modules'][0]['key_takeaway'])
        self.assertIn('應用：', result['modules'][0]['content'])
        self.assertIn('請用實務口吻整理重點。', result['modules'][0]['content'])

    def test_mock_output_keeps_json_structure_and_selected_domains(self):
        with patch.dict(os.environ, {'MOCK_LLM': 'true'}, clear=True):
            result = llm.generate_micro_modules(
                (
                    'Client onboarding requires a service workflow, compliance review, and policy suitability check. '
                    'Teams must communicate the next steps clearly and record each checkpoint for follow-up. '
                    'The document also highlights beneficiary review and document retention expectations.'
                ),
                ['CRM', 'Compliance', 'LifeInsurance'],
                custom_prompt='Emphasize client follow-up actions.',
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result['domains'], ['CRM', 'Compliance', 'LifeInsurance'])
        self.assertEqual(result['total_modules'], len(result['modules']))
        self.assertGreaterEqual(len(result['modules']), 1)
        self.assertTrue(all(module['reading_time_minutes'] == llm.TARGET_READING_TIME_MINUTES for module in result['modules']))
        self.assertTrue(all(module['sequence_order'] >= 1 for module in result['modules']))

    def test_request_structured_output_retries_rate_limits(self):
        class FakeRateLimitError(Exception):
            def __init__(self):
                super().__init__('Rate limit reached. Please try again in 0s.')
                self.status_code = 429
                self.response = type('Response', (), {'headers': {'retry-after': '0'}})()

        class FakeResponse:
            output_text = (
                '{"document_summary":"A concise summary.","domains":["CRM"],'
                '"total_modules":1,"modules":[{"sequence_order":1,"title":"Module",'
                '"content":"Useful training content.","key_takeaway":"Remember this.",'
                '"reading_time_minutes":2}]}'
            )

        class FakeResponsesAPI:
            def __init__(self):
                self.calls = 0

            def create(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise FakeRateLimitError()
                return FakeResponse()

        fake_client = type('Client', (), {'responses': FakeResponsesAPI()})()

        with patch.object(llm, '_create_openai_client', return_value=fake_client):
            with patch.object(llm.time, 'sleep') as sleep_mock:
                result = llm._request_structured_output('test-key', llm.DEFAULT_MODEL, 'prompt')

        self.assertEqual(result['domains'], ['CRM'])
        self.assertEqual(result['total_modules'], 1)
        sleep_mock.assert_called_once()

    def test_request_structured_output_raises_after_retry_budget_exhausted(self):
        class FakeConnectionError(Exception):
            def __init__(self):
                super().__init__('Connection error.')

        class FakeResponsesAPI:
            def create(self, **_kwargs):
                raise FakeConnectionError()

        fake_client = type('Client', (), {'responses': FakeResponsesAPI()})()

        with patch.dict(os.environ, {'OPENAI_MAX_RETRIES': '2'}, clear=False):
            with patch.object(llm, '_create_openai_client', return_value=fake_client):
                with patch.object(llm.time, 'sleep') as sleep_mock:
                    with self.assertRaises(llm.LLMServiceError) as ctx:
                        llm._request_structured_output('test-key', llm.DEFAULT_MODEL, 'prompt')

        self.assertIn('after 2 attempts', str(ctx.exception))
        self.assertEqual(sleep_mock.call_count, 1)


if __name__ == '__main__':
    unittest.main()
