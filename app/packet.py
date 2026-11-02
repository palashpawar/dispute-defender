"""Builds the evidence packet PDF attached to the PayPal provide-evidence call."""
from __future__ import annotations

from fpdf import FPDF

from .store import MerchantStore

_REPL = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "·": "-", "…": "..."}


def _t(s: object) -> str:
    s = str(s)
    for a, b in _REPL.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def build_packet(dispute: dict, plan: dict, store: MerchantStore) -> bytes:
    tx = dispute["disputed_transactions"][0]
    order = store.find_order(tx.get("seller_transaction_id"), tx.get("invoice_number"))
    seller = store.data["seller"]["name"]

    pdf = FPDF(format="Letter")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()

    def h(text: str, size: int = 12) -> None:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", size)
        pdf.multi_cell(0, 7, _t(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)

    def p(text: str) -> None:
        pdf.multi_cell(0, 5.2, _t(text), new_x="LMARGIN", new_y="NEXT")

    h(f"Seller response - PayPal dispute {dispute['dispute_id']}", 15)
    p(f"{seller}  |  Reason: {dispute['reason']}  |  Amount: ${dispute['dispute_amount']['value']} "
      f"{dispute['dispute_amount']['currency_code']}  |  Stage: {dispute['dispute_life_cycle_stage']}")
    p(f"Transaction {tx.get('seller_transaction_id')}  |  Buyer: {tx.get('buyer', {}).get('name', '')}")

    h("Statement")
    p(plan.get("seller_statement", ""))

    h("Key facts")
    for f in plan.get("key_facts", []):
        p(f"- {f['fact']}  [source: {f['source']}]")

    if order:
        h(f"Order {order['order_id']}")
        p(f"Placed {order['created'][:10]} by {order['customer']['name']} <{order['customer']['email']}>")
        p(f"Ship to: {order['ship_to']}")
        for it in order["items"]:
            p(f"  {it['qty']} x {it['name']} ({it['sku']}) @ ${it['price']}")
        p(f"Shipping ${order['shipping']}  |  Total ${order['total']}")

        ship = store.shipment(order["order_id"])
        if ship:
            h(f"Carrier tracking - {ship['carrier']} {ship['tracking_number']}")
            for e in ship["events"]:
                p(f"  {e['time'][:16].replace('T', ' ')}  {e['status']}  ({e['location']})")

        recs = store.fulfillment_records(order["order_id"])
        if recs.get("workshop_attendance"):
            w = recs["workshop_attendance"]
            h("Workshop attendance")
            p(f"Session {w['session'][:10]}, instructor {w['instructor']}. Checked in: {', '.join(w['checked_in'])}.")
            p(f"Listing: {w['listing_text']}")
            p(w["notes"])
        if recs.get("subscription_events"):
            h("Subscription events")
            for e in recs["subscription_events"]:
                p(f"  {e['time'][:16].replace('T', ' ')}  {e['event']}")

        for r in store.refunds(order["order_id"]):
            h("Refunds issued")
            p(f"  {r['time'][:10]}  refund {r['refund_id']}  ${r['amount']}  {r['status']}")

        msgs = store.messages(order["order_id"])
        if msgs:
            h("Customer correspondence")
            for m in msgs:
                who = seller if m["sender"] == "studio" else m["sender"]
                p(f"  {m['time'][:10]} ({m['channel']}) {who}: {m['body']}")

    return bytes(pdf.output())
