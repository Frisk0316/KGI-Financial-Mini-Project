import io
import os
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import database
from llm import generate_micro_modules


def build_modules(title='Module A', content='Useful financial training content.'):
    return {
        'document_summary': 'A concise summary.',
        'domains': ['CRM'],
        'total_modules': 1,
        'modules': [
            {
                'sequence_order': 1,
                'title': title,
                'content': content,
                'key_takeaway': 'Remember the main idea.',
                'reading_time_minutes': 2,
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
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_generate_replaces_existing_domains_and_modules(self):
        doc_id = database.insert_document('trainer_001', 'sample.txt', 'A' * 300)

        with patch.object(app_module, 'generate_micro_modules', return_value=build_modules(title='First Module')):
            first_response = self.client.post('/api/generate', json={'doc_id': doc_id, 'domain_ids': [1, 2]})
        self.assertEqual(first_response.status_code, 200)

        with patch.object(app_module, 'generate_micro_modules', return_value=build_modules(title='Updated Module')):
            second_response = self.client.post('/api/generate', json={'doc_id': doc_id, 'domain_ids': [1]})
        self.assertEqual(second_response.status_code, 200)

        saved_doc = database.get_document_with_modules(doc_id)
        self.assertEqual([domain['domain_id'] for domain in saved_doc['domains']], [1])
        self.assertEqual(len(saved_doc['modules']), 1)
        self.assertEqual(saved_doc['modules'][0]['module_title'], 'Updated Module')

    def test_generate_invalid_module_data_preserves_existing_data(self):
        doc_id = database.insert_document('trainer_001', 'sample.txt', 'B' * 300)
        database.save_generated_content(doc_id, [2], build_modules(title='Stable Module')['modules'])

        with patch.object(app_module, 'generate_micro_modules', side_effect=ValueError('missing modules')):
            response = self.client.post('/api/generate', json={'doc_id': doc_id, 'domain_ids': [3]})

        self.assertEqual(response.status_code, 502)
        payload = response.get_json()
        self.assertIn('invalid data', payload['error'])

        saved_doc = database.get_document_with_modules(doc_id)
        self.assertEqual([domain['domain_id'] for domain in saved_doc['domains']], [2])
        self.assertEqual(saved_doc['modules'][0]['module_title'], 'Stable Module')

    def test_upload_rejects_doc_files(self):
        response = self.client.post(
            '/api/upload',
            data={'file': (io.BytesIO(b'legacy word bytes'), 'legacy.doc')},
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 415)
        self.assertIn('PDF, DOCX, or TXT', response.get_json()['error'])

    def test_generate_requires_integer_domain_ids(self):
        doc_id = database.insert_document('trainer_001', 'sample.txt', 'C' * 300)

        response = self.client.post('/api/generate', json={'doc_id': doc_id, 'domain_ids': ['bad-id']})

        self.assertEqual(response.status_code, 400)
        self.assertIn('valid integers', response.get_json()['error'])

    def test_local_generator_runs_without_external_api(self):
        doc_id = database.insert_document(
            'trainer_001',
            'training.txt',
            (
                'Customer relationship management requires consistent follow-up and clear documentation. '
                'Teams should review each client interaction for service gaps.\n\n'
                'Compliance reviews should verify disclosures, approvals, and record retention before any product recommendation. '
                'Frontline staff benefit from short reminders that reinforce the required steps.'
            ),
        )

        response = self.client.post('/api/generate', json={'doc_id': doc_id, 'domain_ids': [3, 4]})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertGreaterEqual(payload['document_summary'].count('Focus areas:'), 1)
        self.assertGreaterEqual(len(payload['modules']), 1)
        self.assertIn('CRM', payload['modules'][0]['content'])
        self.assertIn('Compliance', payload['modules'][0]['content'])

    def test_generate_micro_modules_returns_structured_output(self):
        result = generate_micro_modules(
            'Estate planning often spans tax, insurance, and wealth preservation decisions.\n\n'
            'Advisors should identify beneficiary needs, review tax exposure, and document follow-up actions.',
            ['WealthManagement', 'TaxRegulations'],
        )

        self.assertEqual(result['total_modules'], len(result['modules']))
        self.assertIn('Focus areas: WealthManagement, TaxRegulations.', result['document_summary'])
        self.assertTrue(result['modules'][0]['title'])
        self.assertGreater(result['modules'][0]['reading_time_minutes'], 0)


if __name__ == '__main__':
    unittest.main()
