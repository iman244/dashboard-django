"""Every call this project makes to object storage.

Deliberately the only such module. The design signs URLs so the operator's
browser uploads straight to the bucket, which keeps DICOM-sized files off a
two-core application server -- but that depends on the browser reaching the
bucket at all. If it cannot, the fallback is to proxy bytes through Django,
and that change should add a view here, not rework the models or the client.

Nothing in this module is imported by the Excel upload path.
"""
import os
import uuid

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from .national_id import normalize_national_id


class S3Unavailable(Exception):
    """Object storage could not be reached or is not configured."""


def is_configured():
    return bool(settings.S3_BUCKET and settings.S3_ACCESS_KEY
                and settings.S3_SECRET_KEY)


def client():
    if not is_configured():
        raise S3Unavailable('Object storage is not configured.')
    return boto3.client(
        's3',
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': settings.S3_ADDRESSING},
        ),
    )


def build_key(monitoring_id, national_id, field_key, filename):
    """Where an upload for this field of this patient lives.

    A uuid rather than the operator's filename: two people uploading `scan.jpg`
    to the same field must not overwrite one another, and the original name is
    kept in the database column that exists for it.
    """
    extension = os.path.splitext(filename or '')[1].lower()[:16]
    folded = normalize_national_id(national_id)
    return (
        f'entries/{monitoring_id}/{folded}/{field_key}/'
        f'{uuid.uuid4().hex}{extension}'
    )


def presign_put(key, content_type):
    """A URL the browser may PUT exactly this object to."""
    try:
        url = client().generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.S3_BUCKET,
                'Key': key,
                'ContentType': content_type,
            },
            ExpiresIn=settings.S3_PRESIGN_TTL,
        )
    except (BotoCoreError, ClientError) as error:
        raise S3Unavailable(str(error)) from error
    return {
        'url': url,
        # The browser must send exactly this, or the signature will not match.
        'headers': {'Content-Type': content_type},
        'expires_in': settings.S3_PRESIGN_TTL,
    }


def presign_get(key):
    """A short-lived read URL; the bucket itself stays private."""
    try:
        return client().generate_presigned_url(
            'get_object',
            Params={'Bucket': settings.S3_BUCKET, 'Key': key},
            ExpiresIn=settings.S3_PRESIGN_TTL,
        )
    except (BotoCoreError, ClientError) as error:
        raise S3Unavailable(str(error)) from error


def head_object(key):
    """`{'size', 'content_type'}` for `key`, or None if it is not there.

    This is what makes a client's claim about its own upload unnecessary to
    trust: the row is committed against what the bucket actually holds.
    """
    try:
        response = client().head_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as error:
        if error.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey'):
            return None
        raise S3Unavailable(str(error)) from error
    except BotoCoreError as error:
        raise S3Unavailable(str(error)) from error
    return {
        'size': response['ContentLength'],
        'content_type': response.get('ContentType', ''),
    }
