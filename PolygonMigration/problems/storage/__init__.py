from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import StorageBackend


def get_storage() -> StorageBackend:
    provider = settings.STORAGE_PROVIDER
    if provider == "s3":
        from .s3 import S3Storage
        return S3Storage(
            settings.AWS_ACCESS_KEY_ID,
            settings.AWS_SECRET_ACCESS_KEY,
            settings.AWS_REGION,
            settings.AWS_S3_ENDPOINT_URL,
        )
    if provider == "azure":
        from .azure import AzureStorage
        return AzureStorage(
            settings.AZURE_STORAGE_ACCOUNT_URL,
            settings.AZURE_TENANT_ID,
            settings.AZURE_CLIENT_ID,
            settings.AZURE_USERNAME,
            settings.AZURE_PASSWORD,
        )
    raise ImproperlyConfigured(
        f"Unsupported STORAGE_PROVIDER '{provider}'. Implemented: azure, s3."
    )


def get_container_name() -> str:
    name = (
        settings.AWS_S3_BUCKET_NAME
        if settings.STORAGE_PROVIDER == "s3"
        else settings.AZURE_CONTAINER_NAME
    )
    if not name:
        raise ImproperlyConfigured("Storage bucket/container name is not set.")
    return name