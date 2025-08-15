# donations/management/commands/pull_paypal_emails.py
import email
import imaplib
import json
import os
import re
from pathlib import Path
from typing import Optional, Tuple

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from donations.utils import parse_paypal_email
from donations import models as donation_models

# Resolve models (your project used Donor/Doner at different times)
Donation = donation_models.Donation
Donor = getattr(donation_models, "Donor", None)
Doner = getattr(donation_models, "Doner", None)  # legacy name fallback


def _imap_connect(host: str, user: str, password: str) -> imaplib.IMAP4_SSL:
    M = imaplib.IMAP4_SSL(host)
    M.login(user, password)
    return M


def _select_folder(M: imaplib.IMAP4_SSL, folder: str) -> None:
    typ, _ = M.select(folder)
    if typ != "OK":
        raise CommandError(f"Could not select folder {folder!r}")


def _search_ids(M: imaplib.IMAP4_SSL, limit: int) -> list[bytes]:
    # Process UNSEEN first; if none, scan last N of ALL
    typ, data = M.search(None, "UNSEEN")
    if typ == "OK":
        ids = [i for i in data[0].split() if i]
        if ids:
            return ids[:limit] if limit else ids
    typ, data = M.search(None, "ALL")
    if typ != "OK":
        return []
    ids = [i for i in data[0].split() if i]
    return ids[-limit:] if (limit and len(ids) > limit) else ids


def _extract_payload(msg: email.message.Message) -> Tuple[str, str]:
    """
    Returns (body_text, content_type_used). Prefers HTML part; falls back to text/plain.
    """
    payload_bytes: Optional[bytes] = None
    charset: Optional[str] = None
    used = "text/plain"

    if msg.is_multipart():
        # prefer HTML
        for part in msg.walk():
            if (part.get_content_type() or "").lower() == "text/html":
                payload_bytes = part.get_payload(decode=True)
                charset = part.get_content_charset()
                used = "text/html"
                break
        if payload_bytes is None:
            for part in msg.walk():
                if (part.get_content_type() or "").lower() == "text/plain":
                    payload_bytes = part.get_payload(decode=True)
                    charset = part.get_content_charset()
                    used = "text/plain"
                    break
    else:
        payload_bytes = msg.get_payload(decode=True)
        charset = msg.get_content_charset()
        used = msg.get_content_type() or "text/plain"

    if payload_bytes is None:
        return "", used

    # Decode robustly
    try_order = [charset, "utf-8", "latin-1"]
    for enc in try_order:
        if not enc:
            continue
        try:
            return payload_bytes.decode(enc, errors="replace"), used
        except Exception:
            continue
    return payload_bytes.decode("utf-8", errors="replace"), used


def _load_state(path: Path) -> set[str]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        # support both list and {"seen_tx_ids":[...]}
        if isinstance(data, dict) and "seen_tx_ids" in data:
            return set(map(str, data["seen_tx_ids"]))
        return set(map(str, data))
    except FileNotFoundError:
        return set()
    except Exception:
        return set()


def _save_state(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(seen)), encoding="utf-8")


class Command(BaseCommand):
    help = "Poll IMAP for PayPal confirmation emails and create Donation rows idempotently."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Parse and show results but do NOT write DB/state.")
        parser.add_argument("--debug", action="store_true", help="Print parse failures with snippets.")
        parser.add_argument("--limit", type=int, default=int(os.getenv("IMAP_LIMIT", "50")), help="Max emails to process.")
        parser.add_argument("--folder", type=str, default=os.getenv("IMAP_FOLDER", "INBOX"), help="Mailbox folder.")
        # boolean with env default + explicit override
        env_mark_seen = os.getenv("IMAP_MARK_SEEN", "true").lower() == "true"
        parser.add_argument("--mark-seen", dest="mark_seen", action="store_true", default=env_mark_seen,
                            help="Mark processed emails as \\Seen.")
        parser.add_argument("--no-mark-seen", dest="mark_seen", action="store_false", help="Do NOT mark as \\Seen.")
        parser.add_argument("--state-file", type=str, default=os.getenv("PAYPAL_INGEST_STATE", ".paypal_ingest_state.json"),
                            help="Path for idempotency state file.")

    def handle(self, *args, **opts):
        host = os.getenv("IMAP_HOST", "imap.gmail.com")
        user = os.getenv("IMAP_USER")
        password = os.getenv("IMAP_PASSWORD")
        if not user or not password:
            raise CommandError("IMAP_USER and IMAP_PASSWORD must be set (env).")

        dry = bool(opts["dry_run"])
        debug = bool(opts["debug"])
        limit = max(1, int(opts["limit"])) if opts["limit"] else 50
        folder = opts["folder"]
        mark_seen = bool(opts["mark_seen"])

        state_path = Path(opts["state_file"])
        if not state_path.is_absolute():
            state_path = Path(os.getcwd()) / state_path
        seen_tx = _load_state(state_path)

        self.stdout.write(self.style.HTTP_INFO(f"[{timezone.now().isoformat()}] Connecting IMAP {host} as {user}…"))
        M = _imap_connect(host, user, password)

        processed = created = already_seen = parse_failed = 0

        try:
            _select_folder(M, folder)
            ids = _search_ids(M, limit)
            if not ids:
                self.stdout.write(self.style.WARNING("No messages to process."))
                return

            for num in ids:
                typ, data = M.fetch(num, "(RFC822)")
                if typ != "OK" or not data:
                    continue

                raw_bytes = data[0][1]
                try:
                    msg = email.message_from_bytes(raw_bytes)
                except Exception:
                    parse_failed += 1
                    continue

                subj = (msg.get("Subject") or "").strip()
                frm = (msg.get("From") or "").strip()

                # Quick allowlist: only attempt parse if it looks like PayPal
                # Don't overfit here; the parser will fail fast otherwise.
                if not (re.search(r"paypal", subj, re.I) or re.search(r"paypal", frm, re.I)):
                    # Count as processed but parse_failed (skipped)
                    processed += 1
                    parse_failed += 1
                    if mark_seen and not dry:
                        M.store(num, "+FLAGS", "\\Seen")
                    continue

                body, ctype = _extract_payload(msg)
                parsed = parse_paypal_email(body)
                processed += 1

                if not parsed:
                    parse_failed += 1
                    if debug:
                        snippet = body[:400].replace("\n", " ")
                        self.stdout.write(self.style.WARNING(f"Could not parse: subj='{subj}' from='{frm}' ctype='{ctype}'"))
                        self.stdout.write(self.style.HTTP_INFO(f"Snippet: {snippet}"))
                    if mark_seen and not dry:
                        M.store(num, "+FLAGS", "\\Seen")
                    continue

                tx = str(parsed["transaction_id"])
                amount = parsed["amount"]  # Decimal
                payer = (parsed.get("payer_name") or "").strip()

                if tx in seen_tx:
                    already_seen += 1
                    if mark_seen and not dry:
                        M.store(num, "+FLAGS", "\\Seen")
                    continue

                # Resolve/create donor if we have a name
                donor_obj = None
                if payer:
                    # case-insensitive match
                    if Donor is not None:
                        donor_obj = Donor.objects.filter(name__iexact=payer).first()
                        if not donor_obj:
                            donor_obj = Donor.objects.create(name=payer)
                    elif Doner is not None:
                        donor_obj = Doner.objects.filter(name__iexact=payer).first()
                        if not donor_obj:
                            donor_obj = Doner.objects.create(name=payer)

                if dry:
                    msg_line = f"[DRY] Would create Donation: {amount} EUR (tx {tx})"
                    if donor_obj or payer:
                        msg_line += f" for donor '{payer or donor_obj.name}'"
                    self.stdout.write(self.style.HTTP_INFO(msg_line))
                    # do NOT add to state in dry-run
                    if mark_seen:
                        # even in dry-run, you may prefer not to mark seen; keep current choice:
                        pass
                    continue

                # Real insert
                with transaction.atomic():
                    kwargs = {"amount": amount}
                    field_names = {f.name for f in Donation._meta.get_fields()}
                    if donor_obj:
                        if "donor" in field_names:
                            kwargs["donor"] = donor_obj
                        elif "doner" in field_names:
                            kwargs["doner"] = donor_obj
                    Donation.objects.create(**kwargs)
                    seen_tx.add(tx)
                    created += 1
                    if mark_seen:
                        M.store(num, "+FLAGS", "\\Seen")

        finally:
            try:
                M.close()
            except Exception:
                pass
            M.logout()

        # Persist idempotency state only after real writes
        if not dry:
            try:
                _save_state(state_path, seen_tx)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Failed to write state: {e}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. processed={processed} created={created} already_seen={already_seen} parse_failed={parse_failed} state={state_path}"
            )
        )
