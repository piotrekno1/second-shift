from functools import partial

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from secondshift.runs.artifacts import delete_output, run_payload
from secondshift.runs.models import Run


@receiver(post_delete, sender=Run, dispatch_uid="runs.delete_run_output")
def delete_run_output(sender: type[Run], instance: Run, **kwargs: object) -> None:
    # Every way a run disappears (admin, retention, cascades) removes its
    # stored input, output and payload. Wait for the commit so a rolled-back
    # delete keeps the file. A storage failure is logged rather than failing an
    # already-committed delete.
    payload = run_payload(instance) or {}
    for path in (instance.output_path, instance.input_path, payload.get("path")):
        if path:
            transaction.on_commit(partial(delete_output, path), robust=True)
