# Dispute Defender

**An AI chargeback analyst for small PayPal merchants.** Dispute Defender pulls your open disputes from the PayPal Disputes API. Claude investigates each one against your own orders, carrier tracking, support inbox and refund ledger, then decides whether to **fight, offer or accept**. You review the draft and send it to PayPal with one click.

> Built for the PayPal AI Hackathon ("Build what's next with PayPal and AI").

[![Dispute Defender demo video](docs/demo-thumbnail.png)](https://youtu.be/XTFwprwivak)

**[Watch the 2½-minute demo on YouTube](https://youtu.be/XTFwprwivak)**: Claude investigating disputes live, the FIGHT/ACCEPT verdicts, and a real PayPal sandbox dispute.

## The problem

Small sellers lose disputes they should win and fight disputes they should concede. Answering a chargeback means digging through tracking pages, emails and refund records, matching them to PayPal's evidence types, and writing a statement before the deadline. A one-person shop with a dozen disputes a month usually just refunds everything, or misses deadlines. Either way it loses revenue and seller standing.

## What it does

| Step | What happens | PayPal capability |
|---|---|---|
| 1. Intake | Disputes land in an AG Grid queue, sorted by response deadline, with revenue at risk and items due within 48h. New disputes arrive by webhook. | Disputes API `GET /v1/customer/disputes`, `CUSTOMER.DISPUTE.CREATED` webhook + signature verification |
| 2. Investigate | Claude runs an agent loop with read-only tools: the PayPal dispute, the linked order, carrier scans, support emails/DMs, refund ledger, customer history, workshop and subscription logs, store policies. The UI streams each lookup live. | Disputes API `GET /v1/customer/disputes/{id}` |
| 3. Decide | Claude returns a structured case plan: **FIGHT / OFFER / ACCEPT**, a calibrated win probability, key facts with sources, evidence items mapped to PayPal `evidence_type`s, a statement for PayPal's reviewer, and a note to the buyer. | |
| 4. Review | You can edit everything: switch the recommendation, untick evidence, rewrite the statement (2000-char limit enforced), change the offer. A confirm dialog shows the exact request body before anything is sent. | |
| 5. Respond | One click runs the matching PayPal call. A generated **evidence packet PDF** is attached automatically. | `provide-evidence` (multipart + `evidence-file`), `make-offer`, `accept-claim`, `send-message` |
| 6. Outcome | In sandbox you can ask PayPal to rule now and watch the queue update. | `require-evidence` / `adjudicate` (sandbox-only) |

A built-in **sandbox shop** (PayPal JS SDK + Orders API v2) creates real sandbox orders, so you can open a real sandbox dispute and watch Dispute Defender handle it end to end.

### The demo merchant

The demo shop is *Fernwood Ceramics*, a fictional pottery studio with 9 disputes, each built to need a different call:

- **Item not received, but UPS shows an adult signature.** Fight with proof of delivery.
- **Item not received, tracking stalled for two weeks.** Don't fight; offer a refund or reship.
- **Cracked bowl, buyer sent a photo.** Offer a refund with return.
- **"Unauthorized", but a repeat customer, same address, and they DM'd a thank-you.** Fight.
- **"Duplicate charge", actually two orders to two addresses.** Fight with order details.
- **"Credit not processed", but the refund ID is on file.** Fight with proof of refund.
- **"Credit not processed", return received and never refunded.** Accept; it's the merchant's fault.
- **Canceled subscription, cancelled after the renewal.** Use judgement.
- **Workshop "not as described", but both seats checked in.** Arrives by simulated webhook.

## AI: how Claude is used

- **Model:** `claude-opus-5-5` through the official Anthropic Python SDK, with adaptive thinking (summaries stream to the UI) and `effort: medium`.
- **Agent loop with tool use:** 8 strict-schema read tools plus a `submit_case_plan` tool whose strict JSON schema *is* the case plan. Independent lookups run as parallel tool calls ([app/agent.py](app/agent.py)).
- **Grounding rules in the system prompt:** cite only facts that came from tool results, never contest a claim the records don't refute, keep win odds calibrated, and flag deadlines under 48h.
- **Human in the loop:** the agent only reads. Every write to PayPal needs the merchant's explicit approval in the UI.
- **Server-side refusal fallbacks** are enabled by default (set `ANTHROPIC_FALLBACKS=0` to disable).
- Without an API key, a clearly labelled **rules engine** ([app/heuristic.py](app/heuristic.py)) fills in so the app stays clickable. It is not the product; Claude is.

## Run it

Requires Python 3.10+.

```bash
git clone https://github.com/palashpawar/dispute-defender && cd dispute-defender
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add ANTHROPIC_API_KEY (and optionally PayPal sandbox keys)
uvicorn app.main:app --port 8000
```

Open http://localhost:8000.

**Mode 1: zero setup (mock sandbox).** With no PayPal keys, the app runs against an in-memory PayPal sandbox that uses the same request and response shapes as the Disputes API. The UI says *PayPal: mock sandbox*. Click **Simulate incoming dispute** to fire a `CUSTOMER.DISPUTE.CREATED` event, or **Analyze all open**.

**Mode 2: real PayPal sandbox.**
1. At [developer.paypal.com](https://developer.paypal.com/dashboard/applications/sandbox), create a sandbox REST app and put its client ID and secret in `.env`.
2. Open http://localhost:8000/shop and buy something with your sandbox **personal** account.
3. Log in to [sandbox.paypal.com](https://www.sandbox.paypal.com) as that buyer and report a problem with the payment.
4. The dispute shows up in the queue. Optionally add a webhook for `CUSTOMER.DISPUTE.*` pointing at `https://<your-host>/webhooks/paypal` and set `PAYPAL_WEBHOOK_ID` so new disputes trigger analysis automatically.

**Deploy:** `render.yaml` is a one-click Render blueprint. Set the env vars in the dashboard.

**Tests:** `pip install -r requirements-dev.txt && pytest`. They cover the mock sandbox flows, the agent loop (with a scripted model), the evidence mapping, and the real client's OAuth and multipart format against a stubbed transport.

## Architecture

```
static/ (vanilla JS + AG Grid)          app/main.py (FastAPI)
  queue · case drawer · SSE trace  <->    /api/disputes, /analyze (SSE), /respond, /webhooks/paypal, /api/shop/*
                                          |-- agent.py    Claude tool-use loop -> case plan
                                          |-- paypal.py   PayPalClient (sandbox REST) | MockPayPal (same interface)
                                          |-- store.py    merchant records (orders, tracking, inbox, refunds)
                                          |-- packet.py   evidence packet PDF
                                          '-- seed.py     demo merchant + disputes
```

## Tools used

- **PayPal:** Disputes API v1, Orders API v2, JS SDK Smart Buttons, Webhooks (with signature verification), sandbox environment.
- **Anthropic Claude** (`claude-opus-5-5`): agentic investigation, structured case plans, statement drafting.
- **AG Grid Community:** the dispute queue.
- FastAPI, fpdf2, httpx. Render (deploy config).

## License

MIT. See [LICENSE](LICENSE).
