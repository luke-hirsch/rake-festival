import email
import imaplib
import json
import os
import re
from pathlib import Path
from typing import List, Optional

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils.timezone import now

from donations.models import Donation
from donations.utils import parse_paypal_email


def _get_text_from_message(msg: email.message.Message) -> str:
    """
    Extract best-effort plain text from an email.message.Message.
    Prefer text/plain, else strip tags from text/html.
    """
    parts: List[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain":
                try:
                    parts.append(part.get_content().strip())
                except Exception:
                    try:
                        parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace"))
                    except Exception:
                        pass
        if not parts:
            # fallback to HTML
            for part in msg.walk():
                ctype = (part.get_content_type() or "").lower()
                if ctype == "text/html":
                    try:
                        html = part.get_content()
                    except Exception:
                        try:
                            html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                        except Exception:
                            html = ""
                    # dumb tag stripper
                    text = re.sub(r"<[^>]+>", " ", html)
                    text = re.sub(r"\s+", " ", text)
                    parts.append(text.strip())
    else:
        ctype = (msg.get_content_type() or "").lower()
        try:
            content = msg.get_content()
        except Exception:
            try:
                content = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                content = ""
        if ctype == "text/plain":
            parts.append(content.strip())
        else:
            # strip HTML if possible
            text = re.sub(r"<[^>]+>", " ", content)
            text = re.sub(r"\s+", " ", text)
            parts.append(text.strip())

    return "\n".join([p for p in parts if p]).strip()


def _load_state(path: Path) -> set:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("seen_tx_ids", []))
    except FileNotFoundError:
        return set()
    except Exception:
        return set()


def _save_state(path: Path, seen: set) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = {"seen_tx_ids": sorted(list(seen))}
    with path.open("w", encoding="utf-8") as f:
        json.dump(tmp, f, ensure_ascii=False, indent=2)


class Command(BaseCommand):
    help = "Poll Gmail via IMAP, parse PayPal payment emails, and create Donation rows idempotently."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Do not write to DB or modify email flags.")
        parser.add_argument("--limit", type=int, default=int(os.getenv("IMAP_LIMIT", "50")), help="Max emails to process this run.")
        parser.add_argument("--folder", type=str, default=os.getenv("IMAP_FOLDER", "INBOX"), help="Mailbox folder to scan.")
        parser.add_argument("--mark-seen", action="store_true", default=os.getenv("IMAP_MARK_SEEN", "true").lower() == "true",
                            help="Mark processed emails as \\Seen (default true).")
        parser.add_argument("--state-file", type=str, default=os.getenv("PAYPAL_INGEST_STATE", ""),
                            help="Custom path for state JSON. Defaults to <BASE_DIR>/.paypal_ingest_state.json")

    def handle(self, *args, **opts):
        host = os.getenv("IMAP_HOST", "imap.gmail.com")
        user = os.getenv("IMAP_USER") or os.getenv("GMAIL_USER")
        pwd = os.getenv("IMAP_PASSWORD") or os.getenv("GMAIL_APP_PASSWORD")
        folder = opts["folder"]
        dry = opts["dry_run"]
        limit = max(1, int(opts["limit"]))
        mark_seen = bool(opts["mark_seen"])

        if not user or not pwd:
            raise CommandError("IMAP_USER/GMAIL_USER and IMAP_PASSWORD/GMAIL_APP_PASSWORD must be set in env.")

        # persistent idempotency state (no model change required)
        default_state_path = Path(getattr(settings, "BASE_DIR", Path.cwd())) / ".paypal_ingest_state.json"
        state_path = Path(opts["state_file"]) if opts["state_file"] else default_state_path
        seen_ids = _load_state(state_path)

        self.stdout.write(self.style.HTTP_INFO(f"[{now().isoformat()}] Connecting IMAP {host} as {user}…"))
        M = imaplib.IMAP4_SSL(host)
        try:
            M.login(user, pwd)
        except imaplib.IMAP4.error as e:
            raise CommandError(f"IMAP login failed: {e}")

        try:
            typ, _ = M.select(folder)
            if typ != "OK":
                raise CommandError(f"Could not select folder {folder!r}")

            # We keep it simple: grab UNSEEN first; if none, also check recent
            # Gmail supports search syntax well.
            typ, data = M.search(None, "UNSEEN")
            if typ != "OK":
                raise CommandError("IMAP search failed")

            ids = [i for i in data[0].split() if i]
            if not ids:
                # nothing unseen; optional: scan last N anyway
                typ, data = M.search(None, "ALL")
                if typ == "OK":
                    all_ids = [i for i in data[0].split() if i]
                    ids = all_ids[-limit:]  # last N
            else:
                ids = ids[:limit]

            if not ids:
                self.stdout.write(self.style.WARNING("No messages to process."))
                return

            processed = 0
            created = 0
            skipped = 0

            for num in ids:
                typ, data = M.fetch(num, "(RFC822)")
                if typ != "OK" or not data:
                    continue

                raw_bytes = data[0][1]
                try:
                    msg = email.message_from_bytes(raw_bytes)
                except Exception:
                    skipped += 1
                    continue

                subj = (msg.get("Subject") or "").strip()
                frm = (msg.get("From") or "").strip()

                # quick filter: only PayPal mails (de/en)
                # you can tune these addresses later if needed
                if not re.search(r"paypal\.[a-z]+", frm, re.IGNORECASE) and "paypal" not in subj.lower():
                    skipped += 1
                    if mark_seen and not dry:
                        # leave non-paypal emails unseen? up to you; marking keeps inbox tidy
                        M.store(num, "+FLAGS", "\\Seen")
                    continue

                body_text = _get_text_from_message(msg)
                # prepend headers as lines so our parser can match "Subject:" etc if needed
                text_for_parser = f"Subject: {subj}\nFrom: {frm}\n\n{body_text}"

                data_parsed = parse_paypal_email(text_for_parser)
                processed += 1

                if not data_parsed:
                    # couldn't parse — mark as seen to avoid loop, but keep count
                    if mark_seen and not dry:
                        M.store(num, "+FLAGS", "\\Seen")
                    continue

                tx_id = data_parsed["transaction_id"]
                amount = data_parsed["amount"]
                currency = data_parsed["currency"]

                if currency != "EUR":
                    # ignore non-EUR to avoid screwing totals
                    if mark_seen and not dry:
                        M.store(num, "+FLAGS", "\\Seen")
                    continue

                if tx_id in seen_ids:
                    skipped += 1
                    if mark_seen and not dry:
                        M.store(num, "+FLAGS", "\\Seen")
                    continue

                if dry:
                    self.stdout.write(self.style.WARNING(f"[DRY] Would create Donation: {amount} EUR (tx {tx_id})"))
                    seen_ids.add(tx_id)
                    continue

                Donation.objects.create(amount=amount)
                created += 1
                seen_ids.add(tx_id)

                if mark_seen:
                    M.store(num, "+FLAGS", "\\Seen")

            # persist idempotency state
            if not dry:
                _save_state(state_path, seen_ids)

            self.stdout.write(self.style.SUCCESS(
                f"Done. processed={processed} created={created} skipped={skipped} state={str(state_path)}"
            ))

        finally:
            try:
                M.close()
            except Exception:
                pass
            M.logout()
