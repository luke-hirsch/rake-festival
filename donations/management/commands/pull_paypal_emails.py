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
           parser.add_argument("--dry-run", action="store_true")
           parser.add_argument("--debug", action="store_true")
           parser.add_argument("--limit", type=int, default=int(os.getenv("IMAP_LIMIT", "50")))
           parser.add_argument("--folder", default=os.getenv("IMAP_FOLDER", "INBOX"))
           parser.add_argument("--mark-seen", default=os.getenv("IMAP_MARK_SEEN", "true").lower() == "true", action="store_true")
           parser.add_argument("--state-file", default=os.getenv("PAYPAL_INGEST_STATE", STATE_DEFAULT))

       def handle(self, *args, **opts):
           host = os.getenv("IMAP_HOST", "imap.gmail.com")
           user = os.getenv("IMAP_USER")
           pw   = os.getenv("IMAP_PASSWORD")
           if not (user and pw):
               self.stderr.write(self.style.ERROR("IMAP_USER/IMAP_PASSWORD missing"))
               return

           dry = opts["dry_run"]
           debug = opts["debug"]
           limit = opts["limit"]
           folder = opts["folder"]
           mark_seen = opts["mark_seen"]
           state_path = Path(opts["state_file"])
           if not state_path.is_absolute():
               state_path = Path(os.getcwd()) / state_path

           # load state
           seen = set()
           if state_path.exists():
               try:
                   seen = set(json.loads(state_path.read_text()))
               except Exception:
                   pass

           self.stdout.write(f"[{timezone.now().isoformat()}] Connecting IMAP {host} as {user}…")

           processed = created = parse_failed = already_seen = 0

           M = imaplib.IMAP4_SSL(host)
           try:
               M.login(user, pw)
               M.select(folder)
               typ, data = M.search(None, 'UNSEEN', 'FROM', '"service@paypal.de"')
               if typ != "OK":
                   typ, data = M.search(None, 'FROM', '"service@paypal.de"')

               ids = data[0].split()
               ids = ids[-limit:] if limit else ids

               for num in ids:
                   typ, msg_data = M.fetch(num, "(RFC822)")
                   if typ != "OK":
                       continue
                   msg = email.message_from_bytes(msg_data[0][1])
                   subj = msg.get("Subject", "")
                   frm = msg.get("From", "")

                   # prefer HTML part, else plain
                   payload = None
                   if msg.is_multipart():
                       for part in msg.walk():
                           ctype = part.get_content_type()
                           if ctype == "text/html":
                               payload = part.get_payload(decode=True)
                               break
                       if not payload:
                           for part in msg.walk():
                               if part.get_content_type() == "text/plain":
                                   payload = part.get_payload(decode=True); break
                   else:
                       payload = msg.get_payload(decode=True)

                   raw = (payload or b"").decode(part.get_content_charset() or "utf-8", errors="replace")
                   data_parsed = parse_paypal_email(raw)
                   processed += 1

                   if not data_parsed:
                       parse_failed += 1
                       if debug:
                           self.stdout.write(self.style.WARNING(f"Could not parse: subj='{subj}' from='{frm}'"))
                       if mark_seen and not dry:
                           M.store(num, "+FLAGS", "\\Seen")
                       continue

                   tx = data_parsed["transaction_id"]
                   amount: Decimal = data_parsed["amount"]
                   payer = (data_parsed.get("payer_name") or "").strip()

                   if tx in seen:
                       already_seen += 1
                       if mark_seen and not dry:
                           M.store(num, "+FLAGS", "\\Seen")
                       continue

                   donor_obj = None
                   if payer:
                       donor_obj = Donor.objects.filter(name__iexact=payer).first()
                       if not donor_obj:
                           donor_obj = Donor.objects.create(name=payer)

                   if dry:
                       self.stdout.write(self.style.HTTP_INFO(f"[DRY] Would create Donation: {amount} EUR (tx {tx})"
                                                              + (f" for donor '{payer}'" if payer else "")))
                   else:
                       with transaction.atomic():
                           kwargs = {"amount": amount}
                           # support FK name donor/doner just in case
                           donation_field_names = {f.name for f in Donation._meta.get_fields()}
                           if donor_obj:
                               if "donor" in donation_field_names:
                                   kwargs["donor"] = donor_obj
                               elif "doner" in donation_field_names:
                                   kwargs["doner"] = donor_obj
                           Donation.objects.create(**kwargs)
                           seen.add(tx)
                           if mark_seen:
                               M.store(num, "+FLAGS", "\\Seen")
                       created += 1

           finally:
               try:
                   M.close()
               except Exception:
                   pass
               M.logout()

           # persist state only on real run
           if not dry:
               try:
                   state_path.write_text(json.dumps(sorted(seen)))
               except Exception as e:
                   self.stderr.write(self.style.ERROR(f"Failed to write state: {e}"))

           self.stdout.write(f"Done. processed={processed} created={created} already_seen={already_seen} parse_failed={parse_failed} state={state_path}")
