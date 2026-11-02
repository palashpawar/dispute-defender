"""PayPal REST clients.

`PayPalClient` talks to the real PayPal sandbox (api-m.sandbox.paypal.com): OAuth2 client
credentials, Disputes API v1, Orders API v2 and webhook signature verification.

`MockPayPal` implements the same interface in memory with the same request/response shapes,
so the app runs end to end with zero credentials. It is clearly labelled "mock" in the UI.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx


class PayPalError(Exception):
    def __init__(self, status: int, body: Any):
        self.status, self.body = status, body
        super().__init__(f"PayPal API error {status}: {body}")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class PayPalClient:
    mode = "sandbox"

    def __init__(self, client_id: str, secret: str, base_url: str, webhook_id: str = ""):
        self.client_id, self.secret, self.base_url, self.webhook_id = client_id, secret, base_url.rstrip("/"), webhook_id
        self._token: str | None = None
        self._token_exp = 0.0
        self._lock = threading.Lock()
        self.http = httpx.Client(base_url=self.base_url, timeout=30)
        self.calls: list[dict] = []  # request log, shown in the UI's API console

    # ---- auth -------------------------------------------------------------------------------
    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_exp - 60:
                return self._token
            r = self.http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                               auth=(self.client_id, self.secret))
            if r.status_code != 200:
                raise PayPalError(r.status_code, r.text)
            body = r.json()
            self._token, self._token_exp = body["access_token"], time.time() + body["expires_in"]
            return self._token

    def _request(self, method: str, path: str, **kw) -> Any:
        headers = {"Authorization": f"Bearer {self._access_token()}", **kw.pop("headers", {})}
        r = self.http.request(method, path, headers=headers, **kw)
        body_log = kw.get("json") or ({"input": json.loads(kw["files"][0][1][1])} if kw.get("files") else None)
        self.calls.append({"time": _now(), "method": method, "path": path, "body": body_log, "status": r.status_code,
                           "debug_id": r.headers.get("paypal-debug-id")})
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = r.text
            raise PayPalError(r.status_code, body)
        return r.json() if r.content else {}

    @staticmethod
    def _multipart(payload: dict, files: list[tuple[str, bytes, str]] | None = None, file_field: str = "evidence-file"):
        parts: list = [("input", (None, json.dumps(payload), "application/json"))]
        for name, content, mime in files or []:
            parts.append((file_field, (name, content, mime)))
        return parts

    # ---- Disputes API -------------------------------------------------------------------------
    def list_disputes(self) -> list[dict]:
        out, path, params = [], "/v1/customer/disputes", {"page_size": 50}
        while path:
            body = self._request("GET", path, params=params)
            out.extend(body.get("items", []))
            nxt = next((l["href"] for l in body.get("links", []) if l.get("rel") == "next"), None)
            path, params = (nxt.replace(self.base_url, ""), None) if nxt else (None, None)
        return out

    def get_dispute(self, dispute_id: str) -> dict:
        return self._request("GET", f"/v1/customer/disputes/{dispute_id}")

    def provide_evidence(self, dispute_id: str, evidences: list[dict], files=None) -> dict:
        return self._request("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence",
                             files=self._multipart({"evidences": evidences}, files))

    def accept_claim(self, dispute_id: str, note: str, accept_claim_type: str = "REFUND",
                     refund_amount: dict | None = None) -> dict:
        payload: dict = {"note": note, "accept_claim_type": accept_claim_type}
        if refund_amount:
            payload["refund_amount"] = refund_amount
        return self._request("POST", f"/v1/customer/disputes/{dispute_id}/accept-claim",
                             files=self._multipart(payload, file_field="accept-claim-document"))

    def make_offer(self, dispute_id: str, note: str, offer_type: str, offer_amount: dict | None) -> dict:
        payload: dict = {"note": note, "offer_type": offer_type}
        if offer_amount:
            payload["offer_amount"] = offer_amount
        return self._request("POST", f"/v1/customer/disputes/{dispute_id}/make-offer", json=payload)

    def send_message(self, dispute_id: str, message: str) -> dict:
        return self._request("POST", f"/v1/customer/disputes/{dispute_id}/send-message",
                             files=self._multipart({"message": message}, file_field="message_document"))

    # Sandbox-only simulation endpoints
    def require_evidence(self, dispute_id: str, action: str = "SELLER_EVIDENCE") -> dict:
        return self._request("POST", f"/v1/customer/disputes/{dispute_id}/require-evidence", json={"action": action})

    def adjudicate(self, dispute_id: str, outcome: str) -> dict:
        return self._request("POST", f"/v1/customer/disputes/{dispute_id}/adjudicate",
                             json={"adjudication_outcome": outcome})

    # ---- Orders API v2 (sandbox shop) ---------------------------------------------------------
    def create_order(self, invoice_id: str, items: list[dict], shipping: str) -> dict:
        item_total = sum(float(i["price"]) * i["qty"] for i in items)
        total = item_total + float(shipping)
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": invoice_id, "invoice_id": invoice_id,
                "amount": {"currency_code": "USD", "value": f"{total:.2f}", "breakdown": {
                    "item_total": {"currency_code": "USD", "value": f"{item_total:.2f}"},
                    "shipping": {"currency_code": "USD", "value": shipping}}},
                "items": [{"name": i["name"], "sku": i["sku"], "quantity": str(i["qty"]),
                           "unit_amount": {"currency_code": "USD", "value": i["price"]}} for i in items],
            }],
        }
        return self._request("POST", "/v2/checkout/orders", json=body,
                             headers={"PayPal-Request-Id": str(uuid.uuid4())})

    def capture_order(self, order_id: str) -> dict:
        return self._request("POST", f"/v2/checkout/orders/{order_id}/capture", json={},
                             headers={"PayPal-Request-Id": str(uuid.uuid4()),
                                      "Prefer": "return=representation"})  # minimal response omits items

    # ---- Webhooks -----------------------------------------------------------------------------
    def verify_webhook(self, headers: dict, event: dict) -> bool:
        if not self.webhook_id:
            return False
        body = {
            "auth_algo": headers.get("paypal-auth-algo"), "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": self.webhook_id, "webhook_event": event,
        }
        return self._request("POST", "/v1/notifications/verify-webhook-signature", json=body).get(
            "verification_status") == "SUCCESS"


class MockPayPal:
    """In-memory stand-in for the PayPal sandbox Disputes API (same shapes, no network)."""

    mode = "mock"

    def __init__(self, disputes: list[dict], incoming: int = 1):
        self._lock = threading.Lock()
        self._disputes = {d["dispute_id"]: copy.deepcopy(d) for d in disputes[:-incoming or None]}
        self._incoming = [copy.deepcopy(d) for d in disputes[len(disputes) - incoming:]] if incoming else []
        self.calls: list[dict] = []  # request log, shown in the UI's API console

    def _log(self, method: str, path: str, body: Any = None) -> None:
        self.calls.append({"time": _now(), "method": method, "path": path, "body": body, "status": 200})

    def _get(self, dispute_id: str) -> dict:
        if dispute_id not in self._disputes:
            raise PayPalError(404, {"name": "RESOURCE_NOT_FOUND", "message": f"Dispute {dispute_id} not found"})
        return self._disputes[dispute_id]

    @staticmethod
    def _require_seller_turn(d: dict) -> None:
        if d["status"] not in ("WAITING_FOR_SELLER_RESPONSE", "OPEN"):
            raise PayPalError(422, {"name": "UNPROCESSABLE_ENTITY",
                                    "details": [{"issue": "NOT_ALLOWED_IN_CURRENT_STATE",
                                                 "description": f"Dispute is {d['status']}"}]})

    def list_disputes(self) -> list[dict]:
        self._log("GET", "/v1/customer/disputes")
        keys = ("dispute_id", "create_time", "update_time", "reason", "status", "dispute_amount",
                "dispute_life_cycle_stage", "dispute_channel", "seller_response_due_date")
        return [{k: d.get(k) for k in keys} for d in self._disputes.values()]

    def get_dispute(self, dispute_id: str) -> dict:
        self._log("GET", f"/v1/customer/disputes/{dispute_id}")
        return copy.deepcopy(self._get(dispute_id))

    def provide_evidence(self, dispute_id: str, evidences: list[dict], files=None) -> dict:
        self._log("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence",
                  {"input": {"evidences": evidences}, "files": [f[0] for f in files or []]})
        with self._lock:
            d = self._get(dispute_id)
            self._require_seller_turn(d)
            for e in evidences:
                if e.get("notes") and len(e["notes"]) > 2000:
                    raise PayPalError(400, {"name": "INVALID_REQUEST", "details": [{"field": "notes", "issue": "MAX_LENGTH"}]})
            d["evidences"].extend({**e, "source": "SUBMITTED_BY_SELLER", "date": _now()} for e in evidences)
            d["status"], d["update_time"] = "UNDER_REVIEW", _now()
        return {"links": [{"href": f"/v1/customer/disputes/{dispute_id}", "rel": "self", "method": "GET"}]}

    def accept_claim(self, dispute_id: str, note: str, accept_claim_type: str = "REFUND", refund_amount=None) -> dict:
        self._log("POST", f"/v1/customer/disputes/{dispute_id}/accept-claim",
                  {"input": {"note": note, "accept_claim_type": accept_claim_type, "refund_amount": refund_amount}})
        with self._lock:
            d = self._get(dispute_id)
            self._require_seller_turn(d)
            amount = refund_amount or d["dispute_amount"]
            d.update(status="RESOLVED", update_time=_now(),
                     dispute_outcome={"outcome_code": "RESOLVED_BUYER_FAVOUR", "amount_refunded": amount})
            d["messages"].append({"posted_by": "SELLER", "time_posted": _now(), "content": note})
        return {"links": [{"href": f"/v1/customer/disputes/{dispute_id}", "rel": "self", "method": "GET"}]}

    def make_offer(self, dispute_id: str, note: str, offer_type: str, offer_amount: dict | None) -> dict:
        self._log("POST", f"/v1/customer/disputes/{dispute_id}/make-offer",
                  {"note": note, "offer_type": offer_type, "offer_amount": offer_amount})
        with self._lock:
            d = self._get(dispute_id)
            self._require_seller_turn(d)
            d.update(status="WAITING_FOR_BUYER_RESPONSE", update_time=_now(),
                     offer={"seller_offered_amount": offer_amount, "offer_type": offer_type,
                            "history": [{"offer_time": _now(), "actor": "SELLER", "event_type": "PROPOSED",
                                         "offer_type": offer_type, "offer_amount": offer_amount, "notes": note}]})
        return {"links": [{"href": f"/v1/customer/disputes/{dispute_id}", "rel": "self", "method": "GET"}]}

    def send_message(self, dispute_id: str, message: str) -> dict:
        self._log("POST", f"/v1/customer/disputes/{dispute_id}/send-message", {"input": {"message": message}})
        with self._lock:
            self._get(dispute_id)["messages"].append({"posted_by": "SELLER", "time_posted": _now(), "content": message})
        return {"links": [{"href": f"/v1/customer/disputes/{dispute_id}", "rel": "self", "method": "GET"}]}

    def require_evidence(self, dispute_id: str, action: str = "SELLER_EVIDENCE") -> dict:
        self._log("POST", f"/v1/customer/disputes/{dispute_id}/require-evidence", {"action": action})
        with self._lock:
            d = self._get(dispute_id)
            d["status"] = "WAITING_FOR_SELLER_RESPONSE" if action == "SELLER_EVIDENCE" else "WAITING_FOR_BUYER_RESPONSE"
        return {}

    def adjudicate(self, dispute_id: str, outcome: str) -> dict:
        self._log("POST", f"/v1/customer/disputes/{dispute_id}/adjudicate", {"adjudication_outcome": outcome})
        with self._lock:
            d = self._get(dispute_id)
            won = outcome == "SELLER_FAVOR"
            d.update(status="RESOLVED", update_time=_now(), dispute_outcome={
                "outcome_code": "RESOLVED_SELLER_FAVOUR" if won else "RESOLVED_BUYER_FAVOUR",
                "amount_refunded": {"currency_code": "USD", "value": "0.00"} if won else d["dispute_amount"]})
        return {}

    def create_order(self, invoice_id: str, items: list[dict], shipping: str) -> dict:
        raise PayPalError(501, "The sandbox shop needs PayPal sandbox credentials (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET).")

    capture_order = create_order

    def verify_webhook(self, headers: dict, event: dict) -> bool:
        return True

    # Mock-only: deliver the next held-back dispute, as if a buyer just filed it.
    def simulate_incoming(self) -> dict | None:
        with self._lock:
            if not self._incoming:
                return None
            d = self._incoming.pop(0)
            d["create_time"] = d["update_time"] = _now()
            self._disputes[d["dispute_id"]] = d
        return {"id": f"WH-{uuid.uuid4().hex[:12].upper()}", "event_type": "CUSTOMER.DISPUTE.CREATED",
                "resource_type": "dispute", "create_time": _now(),
                "resource": {"dispute_id": d["dispute_id"], "reason": d["reason"], "status": d["status"]}}
