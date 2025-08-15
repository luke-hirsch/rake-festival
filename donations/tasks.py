from celery import shared_task
from django.core.management import call_command


@shared_task(name="donations.pull_paypal_emails_task")
def pull_paypal_emails_task(dry_run=False, limit=50, folder="INBOX", mark_seen=True):
    """
    Celery task that delegates to our management command.
    Kept thin so it's easy to test/patch.
    """
    return call_command(
        "pull_paypal_emails",
        dry_run=dry_run,
        limit=limit,
        folder=folder,
        mark_seen=mark_seen,
    )
