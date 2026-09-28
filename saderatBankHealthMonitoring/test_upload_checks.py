import io
from datetime import datetime

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import MonitoringType, SaderatBankHealthMonitoring
from .tests import excel_upload

STEP_2_ROW = {'کد ملی': '0012345678', 'نام': 'Ali', 'علائم عمومي': 'x'}


def codes(issues):
    return {issue['code']: issue for issue in issues}


class UploadChecksTests(APITestCase):
    def setUp(self):
        staff = get_user_model().objects.create_user('op', password='pw', is_staff=True)
        self.client.force_authenticate(staff)

    def upload(self, slug, file, name='Sheet'):
        return self.client.post(reverse('monitorings-upload-excel'),
                                {'name': name, 'type': slug, 'file': file}, format='multipart')

    def test_every_cell_is_stored_as_text(self):
        row = {**STEP_2_ROW, 'تاریخ': datetime(2026, 9, 1), 'Heart rate:': 70, 'BMI': 24.5}
        response = self.upload('step_2', excel_upload([row]))
        self.assertEqual(response.status_code, 200)
        stored = SaderatBankHealthMonitoring.objects.get(id=response.data['id']).json[0]
        self.assertTrue(stored['تاریخ'].startswith('2026-09-01'))
        self.assertEqual(stored['Heart rate:'], '70')
        self.assertEqual(stored['BMI'], '24.5')

    def test_empty_cells_stay_null_not_the_text_nan(self):
        response = self.upload('step_2', excel_upload([{**STEP_2_ROW, 'Respiratory rate': None}]))
        stored = SaderatBankHealthMonitoring.objects.get(id=response.data['id']).json[0]
        self.assertIsNone(stored['Respiratory rate'])

    def test_layout_file_without_its_id_column_is_saved_with_details(self):
        response = self.upload('step_1', excel_upload([STEP_2_ROW]))
        self.assertEqual(response.status_code, 200)
        issue = codes(response.data['issues'])['missing_id_column']
        self.assertEqual(issue['level'], 'warning')
        self.assertEqual(issue['column'], 'personel.کد ملی')
        self.assertIn('کد ملی', issue['found'])
        self.assertEqual(issue['looks_like'], 'step_2')

    def test_empty_sheet_is_saved_with_a_warning(self):
        response = self.upload('step_2', excel_upload([]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(codes(response.data['issues'])['no_rows']['level'], 'warning')

    def test_unreadable_file_is_the_only_refusal(self):
        bad = io.BytesIO(b'not a spreadsheet'); bad.name = 'report.xlsx'
        response = self.upload('step_2', bad)
        self.assertEqual(response.status_code, 400)
        issue = codes(response.data['issues'])['unreadable']
        self.assertEqual(issue['level'], 'error')
        self.assertTrue(issue['detail'])

    def test_bad_ids_are_saved_and_listed_by_excel_row(self):
        rows = [STEP_2_ROW, {**STEP_2_ROW, 'کد ملی': None}, {**STEP_2_ROW, 'کد ملی': 'AB12'}, STEP_2_ROW]
        response = self.upload('step_2', excel_upload(rows))
        self.assertEqual(response.status_code, 200)
        found = codes(response.data['issues'])
        self.assertEqual(found['blank_ids']['rows'], [3])
        self.assertEqual(found['invalid_ids']['rows'], [{'row': 4, 'value': 'AB12'}])
        self.assertEqual(found['duplicate_ids']['groups'], [{'value': '0012345678', 'rows': [2, 5]}])
        self.assertIn('Heart rate:', found['missing_columns']['columns'])
        self.assertTrue(all(issue['level'] == 'warning' for issue in response.data['issues']))

    def test_campaign_without_layout_warns_when_rows_cannot_be_linked(self):
        MonitoringType.objects.create(slug='bp', name_en='BP', name_fa='فشار')
        response = self.upload('bp', excel_upload([{'anything': 1}]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no_id_column', codes(response.data['issues']))

    def test_clean_sheet_has_no_id_warnings(self):
        response = self.upload('step_2', excel_upload([STEP_2_ROW]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(codes(response.data['issues'])), {'missing_columns'})
