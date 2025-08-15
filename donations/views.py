from decimal import Decimal
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView
from django.db.models import Sum
import json
from .models import Donation, Goal
from typing import Optional, Dict


class TotalView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        agg = Donation.objects.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        return JsonResponse({"total": f"{agg.quantize(Decimal('0.01'))}"})

class IndexView(TemplateView):
    template_name = "donations/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        goal = Goal.objects.order_by("-created_at").first()
        if goal:
            target = goal.target_amount
            title = goal.title
            description = goal.description
        else:
            target = Decimal("100.00")
            title = "Fundraiser"
            description = "Hilf mit deiner Spende"

        ctx.update({
            "title": title,
            "target": f"{target.quantize(Decimal('0.01'))}",
            "description": description
        })
        return ctx

class ProgressView(TemplateView):
    """
    Renders a small HTML fragment with total, goal and percent for HTMX polling.
    Template: donations/_progress.html
    """
    template_name = "donations/_progress.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        goal = Goal.objects.order_by("-created_at").first()
        target = goal and goal.target_amount or Decimal("100.00")
        total = Donation.objects.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        pct = 0
        if target > 0:
            pct = int(min(100, (total / target) * 100))
        ctx.update({
            "target": f"{target.quantize(Decimal('0.01'))}",
            "total": f"{total.quantize(Decimal('0.01'))}",
            "percent": pct,
        })
        return ctx

def verify_paypal_order(order_id: str) -> Optional[Dict[str, Decimal]]:
    """
    If PAYPAL_* creds exist, verify an order with PayPal and return:
      {"amount": Decimal("X.YY"), "currency": "EUR"}
    On any issue, return None.
    """
    client_id = getattr(settings, "PAYPAL_CLIENT_ID", None) or os.getenv("PAYPAL_CLIENT_ID")
    secret = getattr(settings, "PAYPAL_SECRET", None) or os.getenv("PAYPAL_SECRET")
    env = (getattr(settings, "PAYPAL_ENV", None) or os.getenv("PAYPAL_ENV") or "sandbox").lower()

    if not client_id or not secret:
        return None  # no creds -> can’t verify

    base = "https://api-m.sandbox.paypal.com" if env != "live" else "https://api-m.paypal.com"

    try:
        # lazy import so tests don’t need requests installed
        import requests

        # 1) OAuth
        auth = (client_id, secret)
        token_resp = requests.post(
            f"{base}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=auth,
            timeout=8,
        )
        if token_resp.status_code != 200:
            return None
        access_token = token_resp.json().get("access_token")
        if not access_token:
            return None

        headers = {"Authorization": f"Bearer {access_token}"}

        # 2) Fetch order details
        r = requests.get(f"{base}/v2/checkout/orders/{order_id}", headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()

        # We want a *captured* payment. Orders API returns captures under:
        # purchase_units[*].payments.captures[*]
        status = data.get("status")
        if status not in {"COMPLETED"}:
            return None

        pu = (data.get("purchase_units") or [])
        if not pu:
            return None
        payments = (pu[0].get("payments") or {})
        captures = payments.get("captures") or []
        if not captures:
            return None

        cap = captures[0]
        if cap.get("status") != "COMPLETED":
            return None

        amount_info = cap.get("amount") or {}
        value = amount_info.get("value")
        currency = amount_info.get("currency_code")
        if not value or not currency:
            return None

        amount = Decimal(str(value)).quantize(Decimal("0.01"))
        return {"amount": amount, "currency": currency}
    except Exception:
        return None


# ---------- capture endpoint (unchanged behavior; now calls real verify) ----------
class CaptureView(View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        # must be JSON
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"error": "invalid_json"}, status=400)

        order_id = payload.get("order_id")
        if not order_id or not isinstance(order_id, str):
            return JsonResponse({"error": "order_id_required"}, status=400)

        result = verify_paypal_order(order_id)
        if not result:
            return JsonResponse({"error": "verification_failed"}, status=400)

        amount = result.get("amount")
        currency = result.get("currency")
        if currency != "EUR":
            return JsonResponse({"error": "unsupported_currency"}, status=400)

        Donation.objects.create(amount=amount)
        return JsonResponse({"status": "created"}, status=201)
