import json
from decimal import Decimal
from unittest.mock import patch
from .utils import parse_paypal_email
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.db import IntegrityError
from donations.models import Donation


class TotalApiTests(TestCase):
    def test_total_zero_returns_0_00(self):
        url = reverse("donations:total")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["total"], "0.00")

    def test_total_sums_decimal_properly(self):
        Donation.objects.create(amount=Decimal("10.00"))
        Donation.objects.create(amount=Decimal("2.35"))
        Donation.objects.create(amount=Decimal("100.00"))
        url = reverse("donations:total")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["total"], "112.35")


class CaptureApiTests(TestCase):
    def test_capture_requires_post_json(self):
        url = reverse("donations:capture")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 405)  # GET not allowed

        r = self.client.post(url, data={"order_id": "x"})  # not JSON
        self.assertEqual(r.status_code, 400)

    def test_capture_missing_order_id(self):
        url = reverse("donations:capture")
        r = self.client.post(url, data=json.dumps({}), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Donation.objects.count(), 0)

    @patch("donations.views.verify_paypal_order")
    def test_capture_verifies_and_creates_donation(self, verify_mock):
        verify_mock.return_value = {"amount": Decimal("12.50"), "currency": "EUR"}
        url = reverse("donations:capture")
        payload = {"order_id": "TEST_ORDER_123"}
        r = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Donation.objects.count(), 1)
        d = Donation.objects.first()
        self.assertEqual(d.amount, Decimal("12.50"))

    @patch("donations.views.verify_paypal_order")
    def test_capture_rejects_if_verification_fails(self, verify_mock):
        verify_mock.return_value = None
        url = reverse("donations:capture")
        payload = {"order_id": "BAD_ORDER"}
        r = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Donation.objects.count(), 0)


class EmailParserTests(SimpleTestCase):
    def test_parse_german_minimal(self):
        raw = """
        Betreff: Sie haben eine Zahlung erhalten
        Transaktionscode: 9AB12345C6789012
        Betrag: 12,50 EUR
        Von: Max Mustermann
        """
        got = parse_paypal_email(raw)
        self.assertIsNotNone(got)
        self.assertEqual(got["transaction_id"], "9AB12345C6789012")
        self.assertEqual(got["currency"], "EUR")
        self.assertEqual(got["amount"], Decimal("12.50"))
        self.assertEqual(got["payer_name"], "Max Mustermann")

    def test_parse_german_thousands_sep(self):
        raw = """
        Transaktionscode: 9ZZ00000Z0000000
        Betrag: 1.234,56 EUR
        Von: Erika Musterfrau
        """
        got = parse_paypal_email(raw)
        self.assertIsNotNone(got)
        self.assertEqual(got["transaction_id"], "9ZZ00000Z0000000")
        self.assertEqual(got["currency"], "EUR")
        self.assertEqual(got["amount"], Decimal("1234.56"))
        self.assertEqual(got["payer_name"], "Erika Musterfrau")

    def test_parse_english_minimal(self):
        raw = """
        Subject: You received a payment
        Transaction ID: 9AB12345C6789012
        Amount: €1,234.56 EUR
        From: John Smith
        """
        got = parse_paypal_email(raw)
        self.assertIsNotNone(got)
        self.assertEqual(got["transaction_id"], "9AB12345C6789012")
        self.assertEqual(got["currency"], "EUR")
        self.assertEqual(got["amount"], Decimal("1234.56"))
        self.assertEqual(got["payer_name"], "John Smith")

    def test_returns_none_when_incomplete(self):
        raw = "Totally unrelated email."
        self.assertIsNone(parse_paypal_email(raw))

class CeleryTaskTests(SimpleTestCase):
    @patch("donations.tasks.call_command")
    def test_task_calls_management_command(self, call_cmd):
        # import inside to avoid import errors before the task exists
        from donations.tasks import pull_paypal_emails_task

        # call the task's .run() directly (no worker needed)
        pull_paypal_emails_task.run(dry_run=True, limit=10, folder="INBOX", mark_seen=False)

        call_cmd.assert_called_once_with(
            "pull_paypal_emails",
            dry_run=True,
            limit=10,
            folder="INBOX",
            mark_seen=False,
        )


class EmailParserRobustnessTests(SimpleTestCase):
    def test_parse_payment_received_de_html(self):
        raw = """
        <html><body>
          <h1>Lukas von Hirschhausen hat dir 1,00 € gesendet</h1>
          <table>
            <tr><td>Erhaltener Betrag</td><td>1,00 € EUR</td></tr>
            <tr><td>Transaktionscode</td><td>8ABCD12345EFG</td></tr>
          </table>
        </body></html>
        """
        got = parse_paypal_email(raw)
        self.assertIsNotNone(got)
        self.assertEqual(got["transaction_id"], "8ABCD12345EFG")
        self.assertEqual(got["currency"], "EUR")
        self.assertEqual(got["amount"], Decimal("1.00"))
        # payer name may be present; if parser doesn’t find it, it’s fine
        self.assertIn("payer_name", got)

    def test_ignore_payment_sent_de(self):
        # Looks legit (has Betrag + Transaktionscode) but is a "sent" mail → should be ignored
        raw = """
        <html><body>
          <h1>Du hast eine Zahlung gesendet</h1>
          <table>
            <tr><td>Betrag</td><td>3,50 € EUR</td></tr>
            <tr><td>Transaktionscode</td><td>9SENT99999999</td></tr>
          </table>
        </body></html>
        """
        self.assertIsNone(parse_paypal_email(raw))

    def test_ignore_withdrawal_success(self):
        raw = """
        <html><body>
          <h1>Ihre Abbuchung war erfolgreich.</h1>
          <p>Sie haben Geld von Ihrem PayPal-Konto auf Ihr Bankkonto abgebucht.</p>
          <table>
            <tr><td>Betrag</td><td>25,00 € EUR</td></tr>
          </table>
        </body></html>
        """
        self.assertIsNone(parse_paypal_email(raw))

    def test_ignore_withdrawal_info(self):
        raw = """
        <html><body>
          <h1>Informationen zur letzten Abbuchung</h1>
          <p>Ihre Abbuchung konnte nicht verarbeitet werden.</p>
          <table>
            <tr><td>Betrag</td><td>25,00 € EUR</td></tr>
          </table>
        </body></html>
        """
        self.assertIsNone(parse_paypal_email(raw))


class MessageIdUniquenessTests(TestCase):
    def test_message_id_unique(self):
        Donation.objects.create(amount=Decimal("1.00"), message_id="msg-abc")
        with self.assertRaises(IntegrityError):
            Donation.objects.create(amount=Decimal("2.00"), message_id="msg-abc")
