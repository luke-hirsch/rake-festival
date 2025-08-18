import html
import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict

# Transaction id (DE/EN)
_TX_RE = re.compile(r"(?:Transaktionscode|Transaction\s*ID)\s*[:\-]?\s*([A-Z0-9\-]{13,19})", re.I)

# Labelled amount (DE/EN), colon optional, supports exotic spaces
_AMT_LABELED_RE = re.compile(
    r"(?:Erhaltener\s+Betrag|Empfangener\s+Betrag|Betrag|Amount)\s*[:\-]?\s*([€\s\d\.,\u00A0\u202F\u2007\u2009]+(?:\s*EUR)?)",
    re.I,
)

# Fallback: first EUR-ish amount anywhere
_AMT_FALLBACK_RE = re.compile(
    r"(€\s*[\d\.,\u00A0\u202F\u2007\u2009]+|\b[\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})\s*(?:€\s*)?EUR\b)",
    re.I,
)

# Payer name from hero line, e.g. "Max Mustermann hat dir ... gesendet"
_PAYER_HERO_RE = re.compile(r"([A-Za-zÄÖÜäöüß .'\-]+?)\s+hat\s+(?:dir|Ihnen)\s+.+?\bgesendet\b", re.I)
# Payer name from labels
_PAYER_LABEL_RE = re.compile(r"(?:From|Von)\s*[:\-]?\s*([^\n]+)", re.I)

# Negative indicators (ignore these mails even if they have Betrag/Tx code)
_NEGATIVE_RE = re.compile(
    r"""(?xi)
    (
      \bDu\shast\s+eine\s+Zahlung\s+gesendet\b |
      \bSie\shaben\s+eine\s+Zahlung\s+gesendet\b |
      \bYou\s+sent\s+a\s+payment\b |
      \bAbbuchung\b | \bwithdrawal\b | \bAuszahlung\b | \bBankkonto\b
    )
    """
)

def _strip_tags_to_text(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</\s*(p|div|td|tr|li|h\d)\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (
        s.replace("\xa0", " ").replace("\u202f", " ")
         .replace("\u2007", " ").replace("\u2009", " ")
         .replace("\r\n", "\n")
    )
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()

def _normalize_amount_to_decimal(amount_str: str) -> Optional[Decimal]:
    if not amount_str:
        return None
    s = (
        amount_str.replace("\xa0", " ").replace("\u202f", " ")
        .replace("\u2007", " ").replace("\u2009", " ")
        .replace("\r", " ").replace("\n", " ")
    ).strip()
    s = re.sub(r"(EUR|eur|€|\s)", "", s)
    has_comma, has_dot = ("," in s), ("." in s)
    if has_comma and has_dot:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        s = s.replace(",", ".")
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None

def parse_paypal_email(raw: str) -> Optional[Dict[str, object]]:
    if not raw:
        return None

    text = _strip_tags_to_text(raw)

    # bail early on non-received mails
    if _NEGATIVE_RE.search(text):
        return None

    txm = _TX_RE.search(text)
    if not txm:
        return None
    transaction_id = txm.group(1).strip()

    amt_match = _AMT_LABELED_RE.search(text) or _AMT_FALLBACK_RE.search(text)
    if not amt_match:
        return None
    amount = _normalize_amount_to_decimal(amt_match.group(1))
    if amount is None:
        return None

    payer_name = ""
    m = _PAYER_LABEL_RE.search(text) or _PAYER_HERO_RE.search(text)
    if m:
        payer_name = m.group(1).strip()

    return {"transaction_id": transaction_id, "amount": amount, "currency": "EUR", "payer_name": payer_name}
