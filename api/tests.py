from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework_simplejwt.tokens import RefreshToken


class TokenLifetimeTests(TestCase):
    """Operators fill long forms and upload images on slow networks.

    SimpleJWT's defaults (a five-minute access token) expired mid-form: each
    image upload then failed with "Given token not valid for any token type".
    """

    def setUp(self):
        user = get_user_model().objects.create_user(username='operator', password='x')
        self.refresh = RefreshToken.for_user(user)

    def lifetime(self, token):
        return token['exp'] - token['iat']

    def test_access_token_outlasts_a_slow_form(self):
        self.assertGreaterEqual(self.lifetime(self.refresh.access_token), 60 * 60)

    def test_refresh_token_lasts_a_working_week(self):
        self.assertGreaterEqual(self.lifetime(self.refresh), 7 * 24 * 60 * 60)
