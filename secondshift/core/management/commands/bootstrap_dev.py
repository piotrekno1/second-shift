import os

import djclick as click
from botocore.exceptions import ClientError
from django.conf import settings

from secondshift.runs.artifacts import artifact_storage
from secondshift.workspaces.owners import ensure_owner


@click.command()
def command() -> None:
    """Create the local owner and workspace when they do not exist."""
    if not settings.DEV:
        raise click.ClickException("bootstrap_dev only runs with SECONDSHIFT_ENV=dev.")
    email = os.environ.get("DEV_ADMIN_EMAIL", "admin@secondshift.local")
    password = os.environ.get("DEV_ADMIN_PASSWORD", "secondshift")
    ensure_owner(email, password)
    if settings.ARTIFACT_STORAGE == "s3":
        ensure_artifact_bucket()
    click.echo(f"Development workspace ready for {email}")


def ensure_artifact_bucket() -> None:
    """Create the local RustFS bucket on first start."""
    storage = artifact_storage()
    client = storage.connection.meta.client
    try:
        client.head_bucket(Bucket=storage.bucket_name)
    except ClientError:
        client.create_bucket(Bucket=storage.bucket_name)
        click.echo(f"Created artifact bucket {storage.bucket_name}")
