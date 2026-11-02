"""Rules-based fallback used only when no Anthropic credentials are configured.

It produces a plan with the same shape as the Claude agent so the app is fully clickable
without an API key, but it is deliberately simple: the UI labels these plans "rules engine"
so nobody mistakes them for the agent's analysis.
"""
from __future__ import annotations

from .store import MerchantStore


def heuristic_plan(dispute: dict, store: MerchantStore) -> dict:
    tx = dispute["disputed_transactions"][0]
    order = store.find_order(tx.get("seller_transaction_id"), tx.get("invoice_number"))
    amount = dispute["dispute_amount"]["value"]
    reason = dispute["reason"]
    plan = {"recommendation": "OFFER", "win_probability": 0.35, "key_facts": [], "evidence": [], "risk_flags": [],
            "offer": None, "accept_claim_type": None, "headline": "", "reasoning": "",
            "seller_statement": "", "buyer_message": ""}
    if not order:
        plan.update(recommendation="ACCEPT", win_probability=0.1, accept_claim_type="REFUND",
                    headline="No matching order found, so there's nothing to contest with.",
                    risk_flags=["Order not found in merchant records"])
        return plan

    oid = order["order_id"]
    ship, refunds = store.shipment(oid), store.refunds(oid)
    delivered = bool(ship and "deliver" in ship["events"][-1]["status"].lower())
    fact = lambda f, src, sup="SELLER": plan["key_facts"].append({"fact": f, "source": src, "supports": sup})

    if reason == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED" and delivered:
        fact(f"{ship['carrier']} shows delivered: {ship['events'][-1]['status']}", f"{ship['carrier']} tracking")
        plan.update(recommendation="FIGHT", win_probability=0.85 if ship.get("signature") else 0.6,
                    headline="Carrier confirms delivery; contest with tracking.")
        plan["evidence"].append({"evidence_type": "PROOF_OF_FULFILLMENT", "notes": "Carrier tracking shows delivery.",
                                 "carrier_name": ship["carrier"], "tracking_number": ship["tracking_number"], "refund_id": None})
    elif reason == "CREDIT_NOT_PROCESSED" and refunds:
        r = refunds[0]
        fact(f"Refund {r['refund_id']} for ${r['amount']} already issued", "refund ledger")
        plan.update(recommendation="FIGHT", win_probability=0.9, headline="Refund was already issued; submit proof of refund.")
        plan["evidence"].append({"evidence_type": "PROOF_OF_REFUND", "notes": f"Refund of ${r['amount']} issued.",
                                 "carrier_name": None, "tracking_number": None, "refund_id": r["refund_id"]})
    elif reason == "CREDIT_NOT_PROCESSED":
        plan.update(recommendation="ACCEPT", win_probability=0.1, accept_claim_type="REFUND",
                    headline="No refund on record; accept and refund.")
    else:
        plan.update(offer={"offer_type": "REFUND_WITH_RETURN", "amount": amount},
                    headline="No decisive evidence found by rules; offer a refund with return.")
    plan["reasoning"] = "Rules engine (no ANTHROPIC_API_KEY configured): matched dispute reason against tracking and refund records only."
    plan["seller_statement"] = f"Order {oid}. " + " ".join(f["fact"] + "." for f in plan["key_facts"])
    plan["buyer_message"] = "Hi, we've reviewed your case and responded through PayPal. Please reach out if we can help."
    return plan
