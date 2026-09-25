# Patient access deployment

Deploy Django before the updated patient client. Apply `python manage.py migrate`, then provision each verified patient from an operator-controlled shell:

```sh
python manage.py provision_patient 0012345678
```

The command creates a new nonstaff user with an opaque username, links a normalized unique ten-digit national ID, and prints a cryptographically generated password once. Deliver the credentials privately offline. Do not run through a job that captures stdout in shared logs. Existing identities cause the command to abort; it never reassigns an existing account or resets a password. Identity verification and delivery are operator responsibilities. There is no patient self-enrollment or identity management HTTP endpoint. Public user creation is disabled; authorized staff may still create ordinary users, without identities.

Patient login is `POST /api/auth/patient/jwt/create/` with `{"national_id":"0012345678","password":"..."}`. Persian and Arabic-Indic national ID digits normalize to ASCII. Successful responses contain `access` and `refresh`; wrong credentials, unknown IDs, inactive users and staff accounts receive a generic 401. Login has a per-IP throttle of 10/minute. Django's default local-memory throttle is per worker; deploy a shared cache and ingress rate limits for multiworker protection.

Use `Authorization: JWT <access>` for `GET /api/saderat-bank-health-monitoring/patient-records/me/`. It returns the existing unpaginated `PatientRecord[]` format, selected solely from the user's identity. Query parameters are rejected. Standard `/api/auth/jwt/refresh/` accepts the refresh token. Clear browser tokens and all patient query caches on sign-out and before another login. Tokens are not immediately revoked by browser sign-out; deactivating a user blocks their requests and refreshes. No credentials or identity are inferred from editable user fields.

General monitoring types, entries (including upload signing), Bank reports and arbitrary `/patient-records/?national_id=...` lookups now require `is_staff` and no patient identity. Existing nonstaff viewer accounts lose these read permissions deliberately. Keep staff and patient accounts separate. Review existing user roles and grant staff status only through an authorized operator process.

Production requires a private `SECRET_KEY` of at least 50 characters; the development key is rejected. Set `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, and `CSRF_TRUSTED_ORIGINS` to explicit comma-separated values. Defaults permit only the known production domains, not localhost or arbitrary origins. Production DEBUG is disabled. Use HTTPS at ingress and a private S3 bucket; returned file URLs are temporary bearer links governed by the existing S3 TTL.

The upstream EHR, laboratory and X-ray service remains outside this authorization boundary. Patient clients must not call it directly. Its owner must restrict network access to an authenticated backend proxy before those patient features return.

Validation in this change: the full Django suite (137 tests), Django system check, migration drift check, and validated OpenAPI export passed with SQLite and the existing sibling virtualenv. That environment reports Django 6.1.1; requirements pin 5.2.7 and were not changed. Repeat migration and integration checks against the pinned deployment environment/PostgreSQL before deployment.
