"""Copy the artifact bucket to or from a directory, or create it.

A stand-in for the S3 provider's own tools (aws s3 sync, rclone).
Usage: s3_copy.py create-bucket|download|upload DIRECTORY
"""

import os
import sys
from pathlib import Path

import boto3

action, directory = sys.argv[1:3]
root = Path(directory)
bucket = os.environ["S3_BUCKET"]
client = boto3.client(
    "s3",
    endpoint_url="http://rustfs:9000",
    aws_access_key_id=os.environ["S3_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET"],
    region_name="us-east-1",
)
if action == "create-bucket":
    client.create_bucket(Bucket=bucket)
elif action == "download":
    count = 0
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            target = root / item["Key"]
            target.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, item["Key"], str(target))
            count += 1
    print(f"Downloaded {count} objects.")
elif action == "upload":
    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        client.upload_file(str(path), bucket, path.relative_to(root).as_posix())
    print(f"Uploaded {len(files)} objects.")
