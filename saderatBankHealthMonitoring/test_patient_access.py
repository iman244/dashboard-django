from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase


class PatientAccessBoundaryTests(APITestCase):
    def test_anonymous_lookup_denied(self):
        response = self.client.get('/api/saderat-bank-health-monitoring/patient-records/', {'national_id': '0012345678'})
        self.assertEqual(response.status_code, 401)

    def test_unprofiled_account_reads_general_endpoints(self):
        user = get_user_model().objects.create_user('outsider', password='pw')
        self.client.force_authenticate(user)
        for endpoint in ('patient-records/?national_id=0012345678', 'monitoring-types/', 'patient-entries/', 'monitorings/'):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get('/api/saderat-bank-health-monitoring/' + endpoint).status_code, 200)

    def test_public_enrollment_disabled(self):
        response = self.client.post('/api/auth/users/', {'username': 'new-patient', 'password': 'secure-test-password-2345'})
        self.assertEqual(response.status_code, 401)


class PatientIdentityAccessTests(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        from .models import PatientIdentity, PatientEntry, MonitoringType
        self.user = get_user_model().objects.create_user('patient-random', password='patient-password')
        self.identity = PatientIdentity.objects.create(user=self.user, national_id=' ۰۰۱۲۳۴۵۶۷۸ ')
        self.entry = PatientEntry.objects.create(monitoring=MonitoringType.objects.first(), national_id='0012345678', values={})
        PatientEntry.objects.create(monitoring=MonitoringType.objects.first(), national_id='0099999999', values={})

    def login(self, national_id='0012345678', password='patient-password'):
        return self.client.post('/api/auth/patient/jwt/create/', {'national_id': national_id, 'password': password})

    def test_credentials_and_own_records(self):
        response = self.login('۰۰۱۲۳۴۵۶۷۸')
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION='JWT ' + response.data['access'])
        response = self.client.get('/api/saderat-bank-health-monitoring/patient-records/me/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.entry.id)
        self.assertEqual(self.client.get('/api/saderat-bank-health-monitoring/patient-records/me/?national_id=0099999999').status_code, 400)
        for endpoint in ('patient-records/?national_id=0099999999', 'monitoring-types/', 'patient-entries/', 'monitorings/', f'patient-entries/{self.entry.id}/'):
            self.assertEqual(self.client.get('/api/saderat-bank-health-monitoring/' + endpoint).status_code, 403)
        self.assertEqual(self.client.post('/api/auth/users/', {'username': 'enroll', 'password': 'test'}).status_code, 403)

    def test_login_errors_do_not_enumerate_and_inactive_is_denied(self):
        wrong = self.login(password='wrong')
        unknown = self.login('0099999999')
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.data, unknown.data)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.login().data, wrong.data)

    def test_unprofiled_and_staff_cannot_use_patient_surface(self):
        for staff in (False, True):
            user = get_user_model().objects.create_user('staff' if staff else 'outsider', is_staff=staff)
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get('/api/saderat-bank-health-monitoring/patient-records/me/').status_code, 403)

    def test_identity_is_normalized_unique_and_validated(self):
        from django.db import IntegrityError, transaction
        from django.core.exceptions import ValidationError
        from .models import PatientIdentity
        self.assertEqual(self.identity.national_id, '0012345678')
        other = get_user_model().objects.create_user('other')
        with self.assertRaises(IntegrityError), transaction.atomic():
            PatientIdentity.objects.create(user=other, national_id='٠٠١٢٣٤٥٦٧٨')
        with self.assertRaises(ValidationError):
            PatientIdentity.objects.create(user=other, national_id='short')

    def test_offline_provisioning_generates_password_and_never_rebinds(self):
        import io
        from django.core.management import call_command, CommandError
        from .models import PatientIdentity
        output = io.StringIO()
        call_command('provision_patient', '0099999999', stdout=output)
        identity = PatientIdentity.objects.get(national_id='0099999999')
        password = output.getvalue().split('Password: ')[1].strip()
        self.assertGreaterEqual(len(password), 24)
        self.assertTrue(identity.user.check_password(password))
        self.assertFalse(identity.user.is_staff)
        with self.assertRaises(CommandError):
            call_command('provision_patient', '0099999999', stdout=io.StringIO())

    def test_expired_and_deactivated_tokens_are_denied(self):
        from datetime import timedelta
        from rest_framework_simplejwt.tokens import AccessToken
        token = AccessToken.for_user(self.user)
        token.set_exp(lifetime=timedelta(seconds=-1))
        self.client.credentials(HTTP_AUTHORIZATION='JWT ' + str(token))
        url = '/api/saderat-bank-health-monitoring/patient-records/me/'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION='JWT ' + self.login().data['access'])
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get(url).status_code, 401)

    def test_second_patient_only_gets_their_entries(self):
        from .models import PatientIdentity
        user = get_user_model().objects.create_user('patient-two', password='patient-password')
        PatientIdentity.objects.create(user=user, national_id='0099999999')
        token = self.login('0099999999').data['access']
        self.client.credentials(HTTP_AUTHORIZATION='JWT ' + token)
        response = self.client.get('/api/saderat-bank-health-monitoring/patient-records/me/')
        self.assertEqual(len(response.data), 1)
        self.assertNotEqual(response.data[0]['id'], self.entry.id)

    def test_patient_cannot_write_or_presign_or_self_promote(self):
        self.client.credentials(HTTP_AUTHORIZATION='JWT ' + self.login().data['access'])
        prefix = '/api/saderat-bank-health-monitoring/'
        for endpoint in ('monitoring-types/', 'patient-entries/', 'monitorings/', 'patient-entries/presign/', 'monitorings/upload_excel/'):
            self.assertEqual(self.client.post(prefix + endpoint, {}, format='json').status_code, 403)
        self.client.patch('/api/auth/users/me/', {'is_staff': True, 'national_id': '0099999999'}, format='json')
        self.user.refresh_from_db()
        self.identity.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertEqual(self.identity.national_id, '0012345678')

    def test_staff_account_cannot_sign_in_as_patient_even_if_linked(self):
        self.user.is_staff = True
        self.user.save()
        self.assertEqual(self.login().status_code, 401)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get('/api/saderat-bank-health-monitoring/patient-records/me/').status_code, 403)
        self.assertEqual(self.client.get('/api/saderat-bank-health-monitoring/patient-entries/').status_code, 403)
