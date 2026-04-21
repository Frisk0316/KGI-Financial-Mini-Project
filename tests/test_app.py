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


def build_batch_modules(doc_ids, domains=None, title_prefix='Module'):
    normalized_doc_ids = [int(doc_id) for doc_id in doc_ids]
    first_doc_id = normalized_doc_ids[0]
    second_doc_id = normalized_doc_ids[1] if len(normalized_doc_ids) > 1 else normalized_doc_ids[0]
    selected_domains = domains or ['CRM']

    return {
        'document_summary': 'A combined summary of the uploaded document batch.',
        'domains': selected_domains,
        'total_modules': 2,
        'modules': [
            {
                'sequence_order': 1,
                'title': f'{title_prefix} 1',
                'content': 'This module combines shared themes across the uploaded files.',
                'key_takeaway': 'Start with the common operating idea.',
                'reading_time_minutes': 2,
                'source_doc_ids': [first_doc_id, second_doc_id],
            },
            {
                'sequence_order': 2,
                'title': f'{title_prefix} 2',
                'content': 'This module focuses on the follow-up implications from the batch.',
                'key_takeaway': 'Connect the summary back to next-step actions.',
                'reading_time_minutes': 2,
                'source_doc_ids': [second_doc_id],
            },
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

    def create_document(self, trainer_id, file_name, text_seed):
        return database.insert_document(trainer_id, file_name, text_seed * 20)

    def test_domains_include_other_tag(self):
        domains = database.get_all_domains()
        self.assertIn('Other', {domain['domain_name'] for domain in domains})

    def test_generate_creates_single_batch_job_and_persists_integrated_modules(self):
        doc_one = self.create_document('trainer_001', 'first.txt', 'Client workflow and compliance review. ')
        doc_two = self.create_document('trainer_001', 'second.txt', 'Insurance planning and client follow-up. ')

        with patch.object(
            app_module,
            'generate_batch_micro_modules',
            return_value=build_batch_modules([doc_one, doc_two], domains=['CRM', 'Compliance']),
        ):
            response = self.client.post(
                '/api/generate',
                json={
                    'doc_ids': [doc_one, doc_two],
                    'domain_ids': [3, 4, 4],
                    'trainer_id': 'trainer_001',
                    'custom_prompt': 'Highlight client-friendly language.',
                },
                headers=self.api_headers(),
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'completed')
        self.assertEqual(payload['total_jobs'], 1)
        self.assertEqual(payload['doc_ids'], [doc_one, doc_two])
        self.assertEqual(payload['custom_prompt'], 'Highlight client-friendly language.')
        self.assertEqual(len(payload['jobs']), 1)
        self.assertEqual(payload['jobs'][0]['batch_id'], payload['batch_id'])

        job_payload = self.client.get(
            f"/api/jobs/{payload['job_id']}",
            headers=self.api_headers(),
        ).get_json()
        self.assertEqual(job_payload['status'], 'completed')
        self.assertEqual(job_payload['requested_domain_ids'], [3, 4])
        self.assertEqual(job_payload['requested_domains'], ['CRM', 'Compliance'])
        self.assertEqual(job_payload['requested_custom_prompt'], 'Highlight client-friendly language.')
        self.assertEqual(job_payload['result']['doc_ids'], [doc_one, doc_two])
        self.assertEqual(len(job_payload['result']['documents']), 2)
        self.assertEqual(len(job_payload['result']['modules']), 2)
        self.assertEqual(job_payload['result']['modules'][0]['source_doc_ids'], [doc_one, doc_two])
        self.assertEqual(job_payload['result']['modules'][0]['source_files'], ['first.txt', 'second.txt'])

        first_doc = database.get_document_with_modules(doc_one, trainer_id='trainer_001')
        second_doc = database.get_document_with_modules(doc_two, trainer_id='trainer_001')
        self.assertEqual({domain['domain_id'] for domain in first_doc['domains']}, {3, 4})
        self.assertEqual({domain['domain_id'] for domain in second_doc['domains']}, {3, 4})
        self.assertEqual(len(first_doc['modules']), 2)
        self.assertEqual(len(second_doc['modules']), 2)
        self.assertEqual(first_doc['modules'][0]['module_title'], 'Module 1')
        self.assertEqual(first_doc['modules'][0]['source_doc_ids'], [doc_one, doc_two])
        self.assertEqual(second_doc['modules'][1]['source_doc_ids'], [doc_two])

    def test_document_route_returns_latest_completed_batch_modules(self):
        doc_id = self.create_document('trainer_owner', 'sample.txt', 'Cross-document planning content. ')
        other_doc_id = self.create_document('trainer_owner', 'extra.txt', 'Related follow-up content. ')

        first_batch_id = database.create_generation_batch(
            [doc_id],
            'trainer_owner',
            [3],
            ['CRM'],
            custom_prompt='',
        )
        database.save_generated_content(
            first_batch_id,
            [doc_id],
            [3],
            'Older summary',
            [{
                'sequence_order': 1,
                'title': 'Old Module',
                'content': 'Old content',
                'key_takeaway': 'Old takeaway',
                'reading_time_minutes': 2,
                'source_doc_ids': [doc_id],
            }],
        )

        second_batch_id = database.create_generation_batch(
            [doc_id, other_doc_id],
            'trainer_owner',
            [3, 4],
            ['CRM', 'Compliance'],
            custom_prompt='',
        )
        database.save_generated_content(
            second_batch_id,
            [doc_id, other_doc_id],
            [3, 4],
            'Newer summary',
            [{
                'sequence_order': 1,
                'title': 'New Module',
                'content': 'New content',
                'key_takeaway': 'New takeaway',
                'reading_time_minutes': 2,
                'source_doc_ids': [doc_id, other_doc_id],
            }],
        )

        response = self.client.get(f'/api/document/{doc_id}', headers=self.api_headers('trainer_owner'))
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['modules'][0]['module_title'], 'New Module')
        self.assertEqual(payload['modules'][0]['source_doc_ids'], [doc_id, other_doc_id])
        self.assertEqual(payload['modules'][0]['source_files'], ['sample.txt', 'extra.txt'])
        self.assertEqual({domain['domain_id'] for domain in payload['domains']}, {3, 4})

    def test_generate_failed_job_preserves_existing_data(self):
        doc_id = self.create_document('trainer_001', 'stable.txt', 'Existing module content. ')
        stable_batch_id = database.create_generation_batch([doc_id], 'trainer_001', [2], ['InvestmentLinked'])
        database.save_generated_content(
            stable_batch_id,
            [doc_id],
            [2],
            'Stable summary',
            [{
                'sequence_order': 1,
                'title': 'Stable Module',
                'content': 'Stable content',
                'key_takeaway': 'Stable takeaway',
                'reading_time_minutes': 2,
                'source_doc_ids': [doc_id],
            }],
        )

        with patch.object(app_module, 'generate_batch_micro_modules', side_effect=ValueError('missing modules')):
            response = self.client.post(
                '/api/generate',
                json={'doc_ids': [doc_id], 'domain_ids': [3], 'trainer_id': 'trainer_001'},
                headers=self.api_headers(),
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'failed')

        job_payload = self.client.get(
            f"/api/jobs/{payload['job_id']}",
            headers=self.api_headers(),
        ).get_json()
        self.assertIn('invalid module data', job_payload['error_message'])

        saved_doc = database.get_document_with_modules(doc_id, trainer_id='trainer_001')
        self.assertEqual([domain['domain_id'] for domain in saved_doc['domains']], [2])
        self.assertEqual(saved_doc['modules'][0]['module_title'], 'Stable Module')

    def test_upload_returns_redacted_preview(self):
        source = 'Contact test@example.com or 0912-345-678 and reference A123456789 for verification.'
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
        doc_id = self.create_document('trainer_owner', 'sample.txt', 'Owner restricted content. ')

        owner_response = self.client.get(f'/api/document/{doc_id}', headers=self.api_headers('trainer_owner'))
        self.assertEqual(owner_response.status_code, 200)
        owner_payload = owner_response.get_json()
        self.assertNotIn('raw_text', owner_payload)
        self.assertIn('preview_text', owner_payload)

        other_response = self.client.get(f'/api/document/{doc_id}', headers=self.api_headers('trainer_other'))
        self.assertEqual(other_response.status_code, 404)

    def test_generate_requires_integer_domain_ids(self):
        doc_id = self.create_document('trainer_001', 'sample.txt', 'Valid content. ')

        response = self.client.post(
            '/api/generate',
            json={'doc_ids': [doc_id], 'domain_ids': ['bad-id'], 'trainer_id': 'trainer_001'},
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
        doc_id = self.create_document('trainer_001', 'training.txt', 'API configuration content. ')

        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post(
                '/api/generate',
                json={'doc_ids': [doc_id], 'domain_ids': [3], 'trainer_id': 'trainer_001'},
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

    def test_generate_batch_micro_modules_runs_two_stage_requests(self):
        documents = [
            {'doc_id': 1, 'file_name': 'doc-one.txt', 'raw_text': 'Client onboarding involves workflow and compliance steps.'},
            {'doc_id': 2, 'file_name': 'doc-two.txt', 'raw_text': 'Follow-up actions connect service and policy review.'},
        ]
        stage_one_payload = {
            'batch_summary': 'The two files share a common onboarding and follow-up theme.',
            'documents': [
                {
                    'doc_id': 1,
                    'file_name': 'doc-one.txt',
                    'summary': 'Document one summary.',
                    'key_points': ['Workflow mapping', 'Compliance checks'],
                },
                {
                    'doc_id': 2,
                    'file_name': 'doc-two.txt',
                    'summary': 'Document two summary.',
                    'key_points': ['Client follow-up', 'Policy review'],
                },
            ],
        }
        stage_two_payload = {
            'document_summary': 'Integrated summary.',
            'domains': ['CRM', 'Compliance'],
            'total_modules': 1,
            'modules': [
                {
                    'sequence_order': 1,
                    'title': 'Integrated Module',
                    'content': 'Combine workflow and follow-up guidance into one module.',
                    'key_takeaway': 'Use both files together.',
                    'reading_time_minutes': 2.7,
                    'source_doc_ids': [1, 2],
                },
            ],
        }

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}, clear=True):
            with patch.object(llm, '_request_structured_output', side_effect=[stage_one_payload, stage_two_payload]) as request_mock:
                result = llm.generate_batch_micro_modules(
                    documents,
                    ['CRM', 'Compliance'],
                    custom_prompt='Emphasize client communication.',
                )

        self.assertEqual(result['domains'], ['CRM', 'Compliance'])
        self.assertEqual(result['total_modules'], 1)
        self.assertEqual(result['modules'][0]['reading_time_minutes'], llm.TARGET_READING_TIME_MINUTES)
        self.assertEqual(result['modules'][0]['source_doc_ids'], [1, 2])
        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(request_mock.call_args_list[0].args[1], llm.DEFAULT_MODEL)
        self.assertIn('Emphasize client communication.', request_mock.call_args_list[0].args[2])
        self.assertIn('doc-one.txt', request_mock.call_args_list[0].args[2])
        self.assertIn('batch_summary', request_mock.call_args_list[1].args[2])
        self.assertIn('Emphasize client communication.', request_mock.call_args_list[1].args[2])

    def test_generate_batch_micro_modules_rejects_domain_mismatch(self):
        documents = [{'doc_id': 1, 'file_name': 'doc-one.txt', 'raw_text': 'Sample text for validation.'}]
        stage_one_payload = {
            'batch_summary': 'Summary',
            'documents': [
                {
                    'doc_id': 1,
                    'file_name': 'doc-one.txt',
                    'summary': 'One summary',
                    'key_points': ['Point A'],
                },
            ],
        }
        mismatched_stage_two_payload = {
            'document_summary': 'Integrated summary.',
            'domains': ['CRM'],
            'total_modules': 1,
            'modules': [
                {
                    'sequence_order': 1,
                    'title': 'Wrong Domain Module',
                    'content': 'This is still structured but uses the wrong domain list.',
                    'key_takeaway': 'Mismatch',
                    'reading_time_minutes': 2,
                    'source_doc_ids': [1],
                },
            ],
        }

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}, clear=True):
            with patch.object(llm, '_request_structured_output', side_effect=[stage_one_payload, mismatched_stage_two_payload]):
                with self.assertRaises(ValueError):
                    llm.generate_batch_micro_modules(documents, ['Compliance'])

    def test_generate_batch_micro_modules_supports_mock_mode_without_api_key(self):
        documents = [
            {
                'doc_id': 1,
                'file_name': 'doc-one.txt',
                'raw_text': 'Client onboarding requires a service workflow, compliance review, and follow-up planning.',
            },
            {
                'doc_id': 2,
                'file_name': 'doc-two.txt',
                'raw_text': 'A second memo adds beneficiary review and product explanation guidance.',
            },
        ]

        with patch.dict(os.environ, {'MOCK_LLM': 'true'}, clear=True):
            result = llm.generate_batch_micro_modules(
                documents,
                ['CRM', 'Compliance', 'LifeInsurance'],
                custom_prompt='Emphasize follow-up actions.',
            )

        self.assertEqual(result['domains'], ['CRM', 'Compliance', 'LifeInsurance'])
        self.assertEqual(result['total_modules'], len(result['modules']))
        self.assertGreaterEqual(len(result['modules']), 1)
        self.assertTrue(all(module['source_doc_ids'] for module in result['modules']))
        self.assertIn('Emphasize follow-up actions.', result['document_summary'])
        self.assertIn('Emphasize follow-up actions.', result['modules'][0]['key_takeaway'])

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
                '"reading_time_minutes":2,"source_doc_ids":[1]}]}'
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
                result = llm._request_structured_output(
                    'test-key',
                    llm.DEFAULT_MODEL,
                    'prompt',
                    'schema_name',
                    llm.MODULES_SCHEMA,
                )

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
                        llm._request_structured_output(
                            'test-key',
                            llm.DEFAULT_MODEL,
                            'prompt',
                            'schema_name',
                            llm.MODULES_SCHEMA,
                        )

        self.assertIn('after 2 attempts', str(ctx.exception))
        self.assertEqual(sleep_mock.call_count, 1)


if __name__ == '__main__':
    unittest.main()
