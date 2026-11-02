"""The merchant's own records: orders, shipments, support inbox, refunds, policies.

This stands in for the shop's database / Shopify / helpdesk. The agent investigates disputes
by querying it through tools. Orders placed through the built-in sandbox shop (PayPal Orders
API) are added here too, so real sandbox disputes link back to real records.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class MerchantStore:
    def __init__(self, seed: dict, path: Path | None = None):
        self._lock = threading.Lock()
        self.path = path
        self.data = {k: v for k, v in seed.items() if k != "disputes"}
        if path and path.exists():
            saved = json.loads(path.read_text())
            # Orders placed through the sandbox shop survive restarts; seed data is regenerated.
            self.data["orders"].extend(saved.get("shop_orders", []))
        # Disputes we learned of by ID (webhook or manual). The sandbox list endpoint can lag by hours.
        self.known_disputes: list[str] = saved.get("known_disputes", []) if path and path.exists() else []

    # ---- lookups used by the agent -------------------------------------------------------
    def find_order(self, transaction_id: str | None = None, invoice: str | None = None) -> dict | None:
        for o in self.data["orders"]:
            if transaction_id and o.get("capture_id") == transaction_id:
                return o
            if invoice and o["order_id"] == invoice:
                return o
        return None

    def order(self, order_id: str) -> dict | None:
        return self.find_order(invoice=order_id)

    def shipment(self, order_id: str) -> dict | None:
        return self.data["shipments"].get(order_id)

    def messages(self, order_id: str) -> list[dict]:
        return [m for m in self.data["inbox"] if m["order_id"] == order_id]

    def refunds(self, order_id: str) -> list[dict]:
        return [r for r in self.data["refunds"] if r["order_id"] == order_id]

    def customer_history(self, email: str) -> list[dict]:
        rows = [
            {"order_id": o["order_id"], "created": o["created"], "total": o["total"],
             "ship_to": o["ship_to"], "status": o["status"]}
            for o in self.data["orders"] if o["customer"]["email"] == email
        ]
        rows += [h for h in self.data["order_history"] if h["customer_email"] == email]
        return sorted(rows, key=lambda r: r["created"])

    def fulfillment_records(self, order_id: str) -> dict:
        return {
            "workshop_attendance": self.data["workshop_logs"].get(order_id),
            "subscription_events": self.data["subscription_events"].get(order_id),
        }

    def policy(self, topic: str) -> str | None:
        return self.data["policies"].get(topic)

    # ---- writes ---------------------------------------------------------------------------
    def add_shop_order(self, order: dict) -> None:
        with self._lock:
            self.data["orders"].append(order)
            self._save()

    def add_known_dispute(self, dispute_id: str) -> None:
        with self._lock:
            if dispute_id not in self.known_disputes:
                self.known_disputes.append(dispute_id)
                self._save()

    def add_refund(self, order_id: str, refund_id: str, amount: str, note: str) -> None:
        with self._lock:
            self.data["refunds"].append({
                "order_id": order_id, "refund_id": refund_id, "amount": amount, "status": "COMPLETED",
                "time": datetime.now(timezone.utc).isoformat(), "note": note,
            })

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        shop = [o for o in self.data["orders"] if o.get("source") == "sandbox_shop"]
        self.path.write_text(json.dumps({"shop_orders": shop, "known_disputes": self.known_disputes}, indent=2))
