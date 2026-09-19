import os
import unittest
from unittest.mock import patch


class WebSecurityConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_app.app as web
        cls.web = web

    def test_startup_rejects_empty_application_password(self):
        with patch.object(self.web.settings, "APP_PASSWORD", ""), \
                self.assertRaisesRegex(RuntimeError, "APP_PASSWORD must be set"):
            self.web._validate_security_configuration()

    def test_startup_rejects_wildcard_origin_with_credentials(self):
        with patch.object(self.web.settings, "ALLOWED_ORIGINS", ["https://desk.example", "*"]), \
                self.assertRaisesRegex(RuntimeError, "must not contain '\\*'"):
            self.web._validate_security_configuration()

    def test_session_cookie_secure_setting_defaults_to_true(self):
        from config.settings import _environment_bool

        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(_environment_bool("SESSION_COOKIE_SECURE", default=True))

    def test_session_cookie_secure_setting_rejects_invalid_value(self):
        from config.settings import _environment_bool

        with patch.dict(os.environ, {"SESSION_COOKIE_SECURE": "sometimes"}, clear=True), \
                self.assertRaisesRegex(RuntimeError, "must be a boolean"):
            _environment_bool("SESSION_COOKIE_SECURE", default=True)
