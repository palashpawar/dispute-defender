import json
import time
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent as agent_mod
from app import main
from app.paypal import MockPayPal, PayPalClient, PayPalError
from app.seed import build_seed
from app.store import MerchantStore

client = TestClient(main.app)


def wait_ready(dispute_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        c = main._case(dispute_id)
        if c["status"] in ("ready", "error"):
            return c
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def test_queue_lists_disputes_with_linked_orders():
    rows = client.get("/api/disputes").json()
    assert len(rows) == 8  # one dispute is held back for the simulated webhook
    assert all(r["order_id"] for r in rows)
    assert rows[0]["due"] <= rows[-1]["due"]


def test_fight_flow_submits_evidence_and_packet():
    did = "PP-D-27137"  # item-not-received, delivered with signature
    with client.stream("POST", f"/api/disputes/{did}/analyze") as r:
        events = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
    assert events[-1]["type"] == "end"
    plan = events[-1]["case"]["plan"]
    assert plan["recommendation"] == "FIGHT" and plan["evidence"][0]["tracking_number"]

    pdf = client.get(f"/api/disputes/{did}/packet.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    res = client.post(f"/api/disputes/{did}/respond").json()
    assert [r["call"] for r in res["results"]][0] == "provide-evidence"
    assert res["dispute"]["status"] == "UNDER_REVIEW"
    call = next(c for c in main.paypal.calls if c["path"].endswith("provide-evidence"))
    assert call["body"]["files"] == [f"{did}-evidence.pdf"]
    types = [e["evidence_type"] for e in call["body"]["input"]["evidences"]]
    assert "PROOF_OF_FULFILLMENT" in types and "MERCHANT_RESPONSE" in types

    ruled = client.post(f"/api/disputes/{did}/simulate-ruling", json={"outcome": "SELLER_FAVOR"}).json()
    assert ruled["dispute_outcome"]["outcome_code"] == "RESOLVED_SELLER_FAVOUR"


def test_accept_flow_and_state_guard():
    did = "PP-D-27959"  # credit not processed, no refund on file
    main.start_analysis(did)
    assert wait_ready(did)["plan"]["recommendation"] == "ACCEPT"
    assert client.post(f"/api/disputes/{did}/respond").json()["dispute"]["status"] == "RESOLVED"
    # PayPal refuses a second response once resolved
    assert client.post(f"/api/disputes/{did}/respond").status_code == 422


def test_merchant_can_override_recommendation():
    did = "PP-D-27274"
    main.start_analysis(did)
    wait_ready(did)
    client.put(f"/api/disputes/{did}/plan", json={"recommendation": "OFFER", "offer": {"offer_type": "REFUND", "amount": "20.00"}})
    res = client.post(f"/api/disputes/{did}/respond").json()
    assert res["results"][0]["call"] == "make-offer"
    assert res["dispute"]["offer"]["seller_offered_amount"]["value"] == "20.00"


def test_simulated_webhook_delivers_and_auto_analyzes():
    r = client.post("/api/simulate/dispute").json()
    assert r["webhook_event"]["event_type"] == "CUSTOMER.DISPUTE.CREATED" and r["analysis_started"]
    wait_ready(r["dispute_id"])
    assert any(row["dispute_id"] == r["dispute_id"] for row in client.get("/api/disputes").json())
    assert client.post("/api/simulate/dispute").status_code == 404


# ---- Claude agent loop, with a scripted stand-in for the Anthropic client -------------------
def _resp(*blocks, stop="tool_use"):
    return NS(stop_reason=stop, content=list(blocks))


def _tool(i, name, inp):
    return NS(type="tool_use", id=f"tu_{i}", name=name, input=inp)


class FakeMessages:
    def __init__(self, script):
        self.script, self.requests = list(script), []

    def create(self, **kw):
        self.requests.append({**kw, "messages": list(kw.get("messages", []))})  # snapshot: the loop mutates the list
        return self.script.pop(0)


def test_agent_loop_runs_tools_and_returns_plan(monkeypatch):
    seed = build_seed()
    store = MerchantStore(seed)
    pp = MockPayPal(seed["disputes"])
    plan = {
        "recommendation": "FIGHT", "win_probability": 1.4, "headline": "Signed for.", "reasoning": "UPS signature.",
        "key_facts": [{"fact": "Signed by M HALE", "source": "UPS tracking", "supports": "SELLER"}],
        "evidence": [{"evidence_type": "PROOF_OF_DELIVERY_SIGNATURE", "notes": "Signed", "carrier_name": "UPS",
                      "tracking_number": "1Z999AA10123456784", "refund_id": None}],
        "seller_statement": "x" * 2500, "buyer_message": "Hi", "offer": None, "accept_claim_type": None, "risk_flags": [],
    }
    fake = FakeMessages([
        _resp(NS(type="thinking", thinking="Start with the dispute."), _tool(1, "get_dispute", {"dispute_id": "PP-D-27137"})),
        _resp(_tool(2, "lookup_order", {"seller_transaction_id": "9TS74213BR558331X", "invoice_number": None}),
              _tool(3, "get_shipment_tracking", {"order_id": "FW-10482"})),
        _resp(NS(type="text", text="Done."), stop="end_turn"),  # forgets to submit -> nudged once
        _resp(_tool(4, "submit_case_plan", plan)),
    ])
    a = agent_mod.DisputeAgent.__new__(agent_mod.DisputeAgent)
    a.paypal, a.store = pp, store
    a.client = NS(messages=fake, beta=NS(messages=fake))
    events = []
    out = a.analyze("PP-D-27137", events.append)

    assert out["win_probability"] == 1.0 and len(out["seller_statement"]) == 2000
    assert [e["name"] for e in events if e["type"] == "tool"] == ["get_dispute", "lookup_order", "get_shipment_tracking"]
    assert any("Delivered - signed" in e["summary"] for e in events if e["type"] == "tool_result")
    # parallel tool calls answered in one user message, in order
    third = fake.requests[2]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in third] == ["tu_2", "tu_3"]
    assert "submit_case_plan" in fake.requests[3]["messages"][-1]["content"]
    req = fake.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["thinking"]["type"] == "adaptive"
    assert "tool_choice" not in req and all(t["strict"] for t in req["tools"])


def test_agent_raises_on_refusal():
    a = agent_mod.DisputeAgent.__new__(agent_mod.DisputeAgent)
    fake = FakeMessages([_resp(stop="refusal")])
    a.client = NS(messages=fake, beta=NS(messages=fake))
    with pytest.raises(agent_mod.AgentError):
        a.analyze("X")


def test_plan_to_evidences_maps_tracking_and_refunds():
    ev = agent_mod.plan_to_evidences({"seller_statement": "s", "evidence": [
        {"evidence_type": "PROOF_OF_FULFILLMENT", "notes": "n", "carrier_name": "ups", "tracking_number": "1Z", "refund_id": None},
        {"evidence_type": "PROOF_OF_REFUND", "notes": "r", "carrier_name": None, "tracking_number": None, "refund_id": "R1"}]})
    assert ev[0]["evidence_info"]["tracking_info"] == [{"carrier_name": "UPS", "tracking_number": "1Z"}]
    assert ev[1]["evidence_info"]["refund_ids"] == ["R1"]
    assert ev[2] == {"evidence_type": "MERCHANT_RESPONSE", "notes": "s"}


# ---- real PayPal client against a stubbed transport ----------------------------------------------
def test_paypal_client_oauth_and_multipart_evidence():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21", "expires_in": 32400})
        if req.url.path.endswith("/provide-evidence"):
            return httpx.Response(200, json={"links": []})
        return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"})

    pc = PayPalClient("id", "secret", "https://api-m.sandbox.paypal.com")
    pc.http = httpx.Client(base_url=pc.base_url, transport=httpx.MockTransport(handler))
    pc.provide_evidence("PP-D-1", [{"evidence_type": "PROOF_OF_REFUND", "notes": "n"}], [("e.pdf", b"%PDF-1", "application/pdf")])
    ev = seen[-1]
    assert ev.headers["authorization"] == "Bearer A21"
    body = ev.read().decode()
    assert 'name="input"' in body and '"evidences"' in body and 'name="evidence-file"; filename="e.pdf"' in body
    with pytest.raises(PayPalError) as e:
        pc.accept_claim("PP-D-1", "ok")
    assert e.value.status == 422
    assert sum(r.url.path == "/v1/oauth2/token" for r in seen) == 1  # token cached
