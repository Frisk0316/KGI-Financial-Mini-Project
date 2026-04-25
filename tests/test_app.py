import io
import hashlib
import json
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
        self.mock_fixture_path = os.path.join(self.temp_dir.name, 'live_fixture.json')
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

    def test_build_generation_result_includes_source_evidence(self):
        docs = [
            {
                'doc_id': 1,
                'file_name': 'tax.txt',
                'raw_text': (
                    'Overview paragraph.\n\n'
                    'Tax treatment depends on product type and income source. '
                    'ETF distributions, bond interest, and capital gains should be judged separately.\n\n'
                    'Closing paragraph.'
                ),
            },
            {
                'doc_id': 2,
                'file_name': 'client.txt',
                'raw_text': (
                    'Client reminder.\n\n'
                    'Explain the difference between dividends and capital gains before making a recommendation.'
                ),
            },
        ]
        llm_result = {
            'document_summary': 'Integrated summary',
            'domains': ['TaxRegulations', 'CRM'],
            'modules': [
                {
                    'sequence_order': 1,
                    'title': 'Tax handling module',
                    'content': (
                        'Key points: - Judge by product type and income source. '
                        '- Separate ETF distributions, bond interest, and capital gains.'
                    ),
                    'key_takeaway': 'Explain the difference between dividends and capital gains.',
                    'reading_time_minutes': 2,
                    'source_doc_ids': [1, 2],
                },
            ],
        }

        payload = app_module._build_generation_result(88, docs, llm_result)
        module = payload['modules'][0]

        self.assertEqual(module['primary_source_doc_id'], 1)
        self.assertEqual([item['doc_id'] for item in module['source_evidence']], [1, 2])
        self.assertEqual(module['source_evidence'][0]['matched_paragraph_index'], 1)
        self.assertIn('product type and income source', module['source_evidence'][0]['matched_text'])
        self.assertIn('capital gains', module['source_evidence'][1]['matched_text'])
        self.assertTrue(module['source_evidence'][0]['matched_terms'])

    def test_domains_include_other_tag(self):
        domains = database.get_all_domains()
        self.assertIn('Other', {domain['domain_name'] for domain in domains})
        self.assertEqual(domains[-1]['domain_name'], 'Other')

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
        self.assertIn('safe_full_text', job_payload['result']['documents'][0])
        self.assertIn('Client workflow', job_payload['result']['documents'][0]['safe_full_text'])
        self.assertEqual(job_payload['result']['modules'][0]['source_doc_ids'], [doc_one, doc_two])
        self.assertEqual(job_payload['result']['modules'][0]['source_files'], ['first.txt', 'second.txt'])
        self.assertEqual(job_payload['result']['modules'][0]['primary_source_doc_id'], doc_one)
        self.assertEqual(
            [item['doc_id'] for item in job_payload['result']['modules'][0]['source_evidence']],
            [doc_one, doc_two],
        )

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

    def test_history_route_returns_saved_batches_for_trainer(self):
        doc_one = self.create_document('trainer_history', 'first.txt', 'History batch content one. ')
        doc_two = self.create_document('trainer_history', 'second.txt', 'History batch content two. ')
        other_doc = self.create_document('trainer_other', 'other.txt', 'Other trainer content. ')

        batch_id = database.create_generation_batch(
            [doc_one, doc_two],
            'trainer_history',
            [3, 4],
            ['CRM', 'Compliance'],
            custom_prompt='History prompt',
        )
        job_id = database.create_generation_job(
            batch_id,
            doc_one,
            'trainer_history',
            [3, 4],
            ['CRM', 'Compliance'],
            custom_prompt='History prompt',
        )
        database.save_generated_content(
            batch_id,
            [doc_one, doc_two],
            [3, 4],
            'History summary',
            [{
                'sequence_order': 1,
                'title': 'History Module',
                'content': 'History module content',
                'key_takeaway': 'History takeaway',
                'reading_time_minutes': 2,
                'source_doc_ids': [doc_one, doc_two],
            }],
        )
        database.update_generation_job(
            job_id,
            'completed',
            result_payload={
                'batch_id': batch_id,
                'doc_ids': [doc_one, doc_two],
                'documents': [],
                'document_summary': 'History summary',
                'domains': ['CRM', 'Compliance'],
                'modules': [],
            },
        )

        other_batch_id = database.create_generation_batch(
            [other_doc],
            'trainer_other',
            [3],
            ['CRM'],
            custom_prompt='Other prompt',
        )
        database.create_generation_job(
            other_batch_id,
            other_doc,
            'trainer_other',
            [3],
            ['CRM'],
            custom_prompt='Other prompt',
        )

        response = self.client.get('/api/history?limit=10', headers=self.api_headers('trainer_history'))
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertEqual(payload['trainer_id'], 'trainer_history')
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['history'][0]['batch_id'], batch_id)
        self.assertEqual(payload['history'][0]['requested_domains'], ['CRM', 'Compliance'])
        self.assertEqual(payload['history'][0]['requested_custom_prompt'], 'History prompt')
        self.assertEqual([doc['file_name'] for doc in payload['history'][0]['documents']], ['first.txt', 'second.txt'])
        self.assertEqual(payload['history'][0]['modules'][0]['module_title'], 'History Module')

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

    def test_generate_batch_micro_modules_persists_live_result_as_mock_fixture(self):
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
                    'reading_time_minutes': 2,
                    'source_doc_ids': [1, 2],
                },
            ],
        }

        with patch.dict(
            os.environ,
            {
                'OPENAI_API_KEY': 'test-key',
                'MOCK_LLM_FIXTURE_PATH': self.mock_fixture_path,
            },
            clear=True,
        ):
            with patch.object(llm, '_request_structured_output', side_effect=[stage_one_payload, stage_two_payload]):
                result = llm.generate_batch_micro_modules(
                    documents,
                    ['CRM', 'Compliance'],
                    custom_prompt='Emphasize client communication.',
                )

        self.assertEqual(result['domains'], ['CRM', 'Compliance'])
        self.assertTrue(os.path.exists(self.mock_fixture_path))

        with open(self.mock_fixture_path, 'r', encoding='utf-8') as file:
            fixture = json.load(file)

        self.assertEqual(fixture['request']['domains'], ['CRM', 'Compliance'])
        self.assertEqual(fixture['request']['custom_prompt'], 'Emphasize client communication.')
        self.assertEqual(fixture['generation_payload']['modules'][0]['source_doc_ids'], [1, 2])

    def test_generate_batch_micro_modules_prefers_saved_live_fixture_in_mock_mode(self):
        fixture = {
            'fixture_version': 1,
            'captured_at': '2026-04-21T00:00:00+00:00',
            'source': 'live_openai_response',
            'model': llm.DEFAULT_MODEL,
            'request': {
                'domains': ['CRM', 'Compliance'],
                'custom_prompt': 'Focus on real fixture replay.',
                'documents': [
                    {
                        'original_doc_id': 101,
                        'file_name': 'doc-one.txt',
                        'text_sha256': hashlib.sha256(
                            'Client onboarding involves workflow and compliance steps.'.encode('utf-8')
                        ).hexdigest(),
                    },
                    {
                        'original_doc_id': 102,
                        'file_name': 'doc-two.txt',
                        'text_sha256': hashlib.sha256(
                            'Follow-up actions connect service and policy review.'.encode('utf-8')
                        ).hexdigest(),
                    },
                ],
            },
            'summary_payload': {
                'batch_summary': 'Saved live summary.',
                'documents': [],
            },
            'generation_payload': {
                'document_summary': 'Saved live integrated summary.',
                'domains': ['CRM', 'Compliance'],
                'total_modules': 1,
                'modules': [
                    {
                        'sequence_order': 1,
                        'title': 'Saved Live Module',
                        'content': 'This module came from a previously captured live result.',
                        'key_takeaway': 'Replay captured outputs when the request matches.',
                        'reading_time_minutes': 2,
                        'source_doc_ids': [101, 102],
                    },
                ],
            },
        }

        with open(self.mock_fixture_path, 'w', encoding='utf-8') as file:
            json.dump(fixture, file, ensure_ascii=False, indent=2)

        documents = [
            {'doc_id': 1, 'file_name': 'doc-one.txt', 'raw_text': 'Client onboarding involves workflow and compliance steps.'},
            {'doc_id': 2, 'file_name': 'doc-two.txt', 'raw_text': 'Follow-up actions connect service and policy review.'},
        ]

        with patch.dict(
            os.environ,
            {
                'MOCK_LLM': 'true',
                'MOCK_LLM_FIXTURE_PATH': self.mock_fixture_path,
            },
            clear=True,
        ):
            result = llm.generate_batch_micro_modules(
                documents,
                ['CRM', 'Compliance'],
                custom_prompt='Use the saved live fixture even if the prompt text changes.',
            )

        self.assertEqual(result['document_summary'], 'Saved live integrated summary.')
        self.assertEqual(result['modules'][0]['title'], 'Saved Live Module')
        self.assertEqual(result['modules'][0]['source_doc_ids'], [1, 2])

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
