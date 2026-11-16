import logging
import boto3
from botocore.config import Config
from .base import StorageBackend

logger = logging.getLogger(__name__)


class S3Storage(StorageBackend):
    def __init__(self, access_key, secret_key, region, endpoint_url=None):
        cfg = Config(
            s3={"addressing_style": "path"} if endpoint_url else {},
            retries={"max_attempts": 3},
        )
        self.client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            endpoint_url=endpoint_url or None,
            config=cfg,
        )

    def upload_bytes(self, container_name, key, data):
        self.client.put_object(Bucket=container_name, Key=key, Body=data)
        logger.info("Uploaded %s/%s", container_name, key)

    def empty_blob(self, container_name, problem_id):
        prefix = f"test_cases/{problem_id}/"
        paginator = self.client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=container_name, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=container_name, Delete={"Objects": objs})
                deleted += len(objs)
        logger.info("Deleted %d objects under %s", deleted, prefix)