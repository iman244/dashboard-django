# MonitoringType field_schema — Design (Slice 1: file uploads)

Date: 2026-09-22
Repos: `dashboard-django` (primary), `dashboard-nextjs` (consumer)

## Goal

Let staff describe, per `MonitoringType`, a loose set of fields that an
operator then fills in per patient — so the Next.js console can render a form
from that description instead of having one hand-built per report type.

Slice 1 proves the whole road end to end with **one field type: `file`**.
An operator opens a monitoring, types a national ID, and uploads one or many
files into each declared file field (MRI images, XMS scans), then reads them
back.

## Non-goals, stated plainly

**The Excel path is untouched. This is the load-bearing constraint.**

`SaderatBankHealthMonitoringUploadExcelSerializer` and the `upload_excel`
action are not modified, not wrapped, and not validated against anything in
this feature. Specifically, this feature does NOT:

- validate Excel columns against `field_schema`, or require any relationship
  between them;
- add size, MIME, count or content limits to the Excel upload;
- let a `field_schema` — present, absent, or mid-edit — block or alter an
  Excel upload;
- make uploading an Excel affect existing entries, or entries affect Excel.

`field_schema` and the Excel blob are **orthogonal capabilities on the same
type**, not a discriminated union. A type may have both, either, or neither,
and no consumer ever branches on which. `step_1` and `step_2` can gain a
`field_schema` later without their `json` blobs being touched.

A corollary that must stay true: **the schema-edit guard counts `PatientEntry`
rows only, never rows inside `json`.** If it read the blob, an Excel upload
could retroactively make a schema uneditable — exactly the coupling ruled out
above.

Also out of scope for slice 1: the other five field types, sections, the
destructive-change 409 guard, and a UI for authoring `field_schema` (set it
through DRF or Django admin for now).

## Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Additive and parallel to Excel | See non-goals. Composition, not a union. |
| 2 | One entry per `(monitoring, national_id)`, editable | Mirrors the existing `unique_monitoring_name_type` idiom. |
| 3 | Rich validation *eventually*; slice 1 ships `file` only | Unsupported types are rejected, not half-supported, so slice 2 adds them without renegotiation. |
| 4 | Destructive schema changes → 409 (slice 2) | Echoes `destroy()`'s existing `ProtectedError` → 409. |
| 5 | Presigned direct-to-S3 (approach B) | Keeps DICOM-sized uploads off a two-core VM behind Traefik. |
| 6 | `national_id` is structural, not a declarable field | It is the entry's identity column; a schema declaring that key is rejected. |
| 7 | boto3 directly; **no django-storages** | The browser writes the bytes, not Django. A `FileField` would imply Django owns the write. We store the key as a `CharField` and sign URLs with boto3. Fewer deps, honest semantics. |

## Schema contract (slice 1)

Stored in `MonitoringType.field_schema`, JSONB, default `{}`.

```json
{
  "version": 1,
  "fields": [
    {
      "key": "mri_image",
      "type": "file",
      "label_en": "MRI Image",
      "label_fa": "تصویر ام‌آر‌آی",
      "required": true,
      "multiple": true,
      "accept": ["image/jpeg", "image/png", "application/dicom"],
      "max_size_mb": 50,
      "max_count": 10
    },
    {
      "key": "xms",
      "type": "file",
      "label_en": "XMS",
      "label_fa": "ایکس‌ام‌اس",
      "required": false,
      "multiple": true,
      "accept": ["*/*"],
      "max_size_mb": 25,
      "max_count": 5
    }
  ]
}
```

Rules the validator enforces:

- `version` must be `1`.
- `fields` is a list; `key` matches `^[a-z][a-z0-9_]*$`, unique within the
  schema, and may not be `national_id` (reserved — it is a column).
- `type` must be `file` in slice 1. Any other value is a validation error
  naming the types that exist, so slice 2 is a pure addition.
- `label_en` and `label_fa` are both required and non-empty.
- `accept` is a list of MIME patterns (`*/*` and `image/*` allowed).
- `max_size_mb` is a positive integer; `max_count` a positive integer,
  meaningful only when `multiple` is true.
- An empty `{}` is valid and means "no fields declared yet".

**Why labels live in the schema rather than `messages/*.json`:** next-intl
namespaces are static files compiled into the bundle; these labels are authored
at runtime by staff. Runtime-authored copy cannot live in a build-time
catalogue. Requiring both locales on every label is how this feature honours
the project's no-hardcoded-strings rule — the Persian operator never sees an
English label leak through because someone filled in only one.

## Data model

```
MonitoringType
  + field_schema  JSONField(default=dict, blank=True)

PatientEntry                                    # new
  monitoring   FK -> SaderatBankHealthMonitoring, on_delete=CASCADE
  national_id  CharField(max_length=10, db_index=True)
  values       JSONField(default=dict, blank=True)   # empty in slice 1
  created_at, updated_at
  UniqueConstraint(monitoring, national_id)

PatientEntryFile                                # new
  entry         FK -> PatientEntry, on_delete=CASCADE, related_name='files'
  field_key     CharField(max_length=64, db_index=True)
  key           CharField(max_length=512, unique=True)   # the S3 object key
  original_name CharField(max_length=255)
  content_type  CharField(max_length=127)
  size          PositiveBigIntegerField()
  uploaded_at
```

`monitoring` cascades rather than protects: an entry is *part of* a monitoring,
unlike a `MonitoringType`, which is referenced by many and must outlive them.

File metadata lives in Postgres while bytes live in S3. That split is what
makes the slice-2 guard answerable as an indexed
`WHERE field_key = '…'` query, instead of a scan through JSONB.

`values` is present but unused in slice 1 — it is where `string`/`number`/etc.
land in slice 2. Shipping the column now avoids a second migration.

### national_id is normalized server-side

Operators type on a Persian keyboard, which produces `۰۱۲`; some source data
carries Arabic-Indic `٠١٢`. `dashboard-nextjs` already normalizes these at
every network boundary (`digitsFaToEn` in `_data/ehr-params.ts`,
`digitsArToEn` + `digitsFaToEn` in `_ehr/classify.ts`).

For this feature that convention is **not sufficient**, because `national_id`
is an identity key under a unique constraint. `۰۰۱۲۳۴۵۶۷۸` and `0012345678`
are different strings, so the constraint would accept both and create two
entries for one patient — each holding half their files, with no error raised
anywhere. The constraint would look like it protects identity while silently
failing to.

Therefore: **Django normalizes Persian and Arabic-Indic digits to ASCII before
any lookup, save, or S3 key construction.** A `normalize_national_id()` helper
does this in one place and is applied in the serializer's `validate_national_id`,
so no code path can skip it. The client normalizing as well is good UX, not the
guarantee.

Beyond digit folding the value stays loose in slice 1 — non-empty, at most 10
characters, no checksum. Requiring a valid Iranian national ID would couple
this feature to assumptions the Excel path does not make.

### Orphaned objects

A presigned upload that succeeds while the entry save never happens leaves an
object in the bucket with no row pointing at it. Slice 1 accepts this: keys are
namespaced by monitoring and national id, so orphans are identifiable, and no
correctness property depends on their absence. A reaper (list objects, drop any
key older than a day with no `PatientEntryFile`) is slice 2 work and is noted
here so it is not mistaken for an oversight.

### S3 key layout

```
entries/{monitoring_id}/{national_id}/{field_key}/{uuid4}{ext}
```

A UUID rather than the original filename: two operators uploading `scan.jpg`
for the same field must not collide, and the original name is preserved in the
database column where it belongs.

## API surface

All under `/api/saderat-bank-health-monitoring/`, registered on the existing
`DefaultRouter`, authenticated with `JWTAuthentication` + `IsAuthenticated`.

`MonitoringTypeSerializer.Meta.fields` gains `field_schema`. **This is the only
existing line of code the feature changes.**

```
POST   patient-entries/presign/
       {monitoring, national_id, field_key, filename, content_type, size}
    -> {upload_url, key, expires_in, headers}
       Validates: monitoring exists; its type declares field_key as a file
       field; content_type satisfies accept; size <= max_size_mb.
       Signs nothing it has not checked.

GET    patient-entries/?monitoring=<id>[&national_id=<nid>]
GET    patient-entries/{pk}/
POST   patient-entries/            {monitoring, national_id, files:[...]}
PATCH  patient-entries/{pk}/       {files:[...]}
DELETE patient-entries/{pk}/
```

`files[]` entries are `{field_key, key, original_name, content_type, size}`.
Before committing a row, Django issues a `HEAD` on each key and rejects the
request unless the object exists and its real `ContentLength` matches the
declared `size`. **A client's claim about what it uploaded is never trusted.**

Create/update follow the pattern the console already uses for monitoring types:
the client looks the entry up by `(monitoring, national_id)`, then POSTs or
PATCHes. `POST` on an existing pair returns 409 with the existing id.

Reads return each file with a short-lived presigned **GET** url; the bucket
stays private.

## Settings

Following the `django-environ` idiom already in `settings/base.py`:

```python
S3_ENDPOINT_URL  = env('S3_ENDPOINT_URL', default='https://s3.ir-thr-at1.arvanstorage.ir')
S3_ACCESS_KEY    = env('S3_ACCESS_KEY', default='')
S3_SECRET_KEY    = env('S3_SECRET_KEY', default='')
S3_BUCKET        = env('S3_BUCKET', default='')
S3_REGION        = env('S3_REGION', default='ir-thr-at1')
S3_ADDRESSING    = env('S3_ADDRESSING', default='virtual')   # 'path' if Arvan needs it
S3_PRESIGN_TTL   = env.int('S3_PRESIGN_TTL', default=900)
```

Deployment needs `S3_*` added to `ENV_KEYS_DJANGO` in
`mainreport-platform/deploy.sh` and the matching GitHub secrets set by the user
(`gh secret set` is a blocked action for the agent).

The bucket needs a CORS rule permitting `PUT` from the dashboard origin — this
is what makes approach B work, and its absence is the likeliest first failure.

## Validation ownership

The rules live in `field_schema` but are interpreted twice: Django validates on
presign and on commit; the Next.js form reads the same JSON to drive inline
errors and to filter the file picker.

**Django is the authority. The client's copy is a UX convenience.** The same
honesty the patient-portal spec applies to its sign-in gate applies here: a
future reader must not assume the form is a guarantee.

## Known risk — S3 reachability

From the development Mac on 2026-09-22, `s3.ir-thr-at1.arvanstorage.ir`
resolves and accepts TCP on :443, but the TLS handshake dies with
`unexpected eof while reading` — the same signature seen against
`mainreport.ir` and SSH the same day. The endpoint is presumed healthy; the
development network is not.

This matters because **approach B uploads from the operator's browser**. If
operators sit behind networks that behave this way, browser→S3 fails regardless
of backend correctness. The fallback is approach C: a Django endpoint that
proxies bytes to S3.

To keep that fallback cheap, all S3 interaction is confined to one module
(`saderatBankHealthMonitoring/s3.py`). Switching to C means adding an upload
view that calls into it — not reworking the models, the schema, or the client's
data layer.

## Slice roadmap

- **Slice 1 (this spec)** — `file` fields, `national_id`, presigned upload,
  form renderer, read-back.
- **Slice 2** — `string`, `number`, `date`, `boolean`, `choice`; `values`
  populated; `pattern`/`min`/`max`; the destructive-change 409 guard.
- **Slice 3** — sections, a schema-authoring UI on `/console/monitoring-types`.
