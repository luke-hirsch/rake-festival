# donations/utils.py
import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict

# PayPal IDs vary; be flexible
_TX_RE = re.compile(r"(?:Transaction\s*ID|Transaktionscode)\s*:\s*([A-Z0-9\-]{13,19})", re.IGNORECASE)
_AMT_RE = re.compile(r"(?:Amount|Betrag)\s*:\s*([€\s]*[\d.,\s]+)", re.IGNORECASE)
_FROM_RE = re.compile(r"(?:From|Von)\s*:\s*(.+)", re.IGNORECASE)


def _normalize_amount_to_decimal(amount_str: str) -> Optional[Decimal]:
    if not amount_str:
        return None
    s = amount_str.replace("\xa0", " ").strip()
    s = re.sub(r"(EUR|eur|€|\s)", "", s)

    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma and not has_dot:
        s = s.replace(",", ".")
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def parse_paypal_email(raw: str) -> Optional[Dict[str, object]]:
    if not raw:
        return None
    text = raw.replace("\r\n", "\n")

    txm = _TX_RE.search(text)
    amt = _AMT_RE.search(text)
    payer = _FROM_RE.search(text)

    if not txm or not amt:
        return None

    transaction_id = txm.group(1).strip()
    amount = _normalize_amount_to_decimal(amt.group(1))
    if amount is None:
        return None

    payer_name = payer.group(1).strip() if payer else ""

    return {
        "transaction_id": transaction_id,
        "amount": amount,
        "currency": "EUR",
        "payer_name": payer_name,
    }
