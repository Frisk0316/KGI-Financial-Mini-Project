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
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DB_PATH = os.path.join(self.temp_dir.name, 'test.db')
        database.init_db()
        app_module.app.config['TESTING'] = True
        app_module.app.config['INLINE_GENERATION_JOBS'] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        app_module.app.config['INLINE_GENERATION_JOBS'] = False
        self.temp_dir.cleanup()

    def api_headers(self, trainer_id='trainer_001'):
        return {'X-Trainer-Id': trainer_id}

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
        source = '聯絡信箱 test@example.com，手機 0912-345-678，身分證字號 A123456789。'
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
                )

        self.assertEqual(result['total_modules'], len(result['modules']))
        self.assertEqual(result['domains'], ['WealthManagement', 'TaxRegulations'])
        self.assertEqual(result['modules'][0]['reading_time_minutes'], llm.TARGET_READING_TIME_MINUTES)
        self.assertEqual(request_mock.call_args.args[1], llm.DEFAULT_MODEL)
        self.assertIn('domains: WealthManagement, TaxRegulations', request_mock.call_args.args[2])

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


if __name__ == '__main__':
    unittest.main()
