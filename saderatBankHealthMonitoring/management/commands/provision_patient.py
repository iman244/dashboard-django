"""Run only from an operator-controlled shell; deliver the output offline."""
import secrets

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from saderatBankHealthMonitoring.models import PatientIdentity
from saderatBankHealthMonitoring.national_id import normalize_national_id


class Command(BaseCommand):
    help = 'Create a patient account and print a generated password for offline delivery.'

    def add_arguments(self, parser):
        parser.add_argument('national_id')

    def handle(self, *args, **options):
        national_id = normalize_national_id(options['national_id'])
        password = secrets.token_urlsafe(24)
        try:
            with transaction.atomic():
                user = get_user_model().objects.create_user(
                    username='patient_' + secrets.token_hex(16), password=password,
                    is_staff=False, is_superuser=False)
                PatientIdentity.objects.create(user=user, national_id=national_id)
        except (ValidationError, IntegrityError) as error:
            raise CommandError('Invalid national ID or an account already exists; no account was changed.') from error
        self.stdout.write('Deliver these credentials privately offline; do not store them in logs.')
        self.stdout.write(f'National ID: {national_id}\nPassword: {password}')
