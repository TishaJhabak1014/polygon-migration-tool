from .base import StorageBackend


class AzureStorage(StorageBackend):
    def __init__(self, account_url, tenant_id, client_id, username, password):
        from problems.AzureTestcase import AzureBlobManager
        self._mgr = AzureBlobManager(account_url, tenant_id, client_id, username, password)

    def upload_bytes(self, container_name, key, data):
        client = self._mgr.blob_service_client.get_blob_client(container=container_name, blob=key)
        client.upload_blob(data, overwrite=True)

    def empty_blob(self, container_name, problem_id):
        self._mgr.empty_blob(container_name, problem_id)