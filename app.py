"""Entrypoint for a serverless host (Vercel loads the top-level `app`): the hosted build of SubSweep.

Run SubSweep on your own computer (`python -m subsweep`) for the version that keeps everything in one process; that build is untouched.
This one is for a link anyone can open. A serverless host keeps no state for a visitor (every request may be a different instance), so the
browser keeps the statement and re-sends it with each action; the server analyses it in memory, keeps nothing, and writes nothing to disk.
See create_app and StatelessMiddleware in subsweep/api/app.py.

Environment (all optional):
  SUBSWEEP_HOSTED_UPLOADS=off   sample statement only, no uploads
  SUBSWEEP_HOSTED_AI=on         let Claude word the explanations (needs ANTHROPIC_API_KEY); off by default, because a public endpoint
                                would otherwise spend the operator's API credits
"""
import os

os.environ["PAYPAL_MODE"] = "mock"          # a public build never calls PayPal, whatever else is set in the environment

from subsweep.ai import AIAssistant, AuditLog, build_default_llm  # noqa: E402
from subsweep.api.app import create_app  # noqa: E402

_llm = build_default_llm() if os.environ.get("SUBSWEEP_HOSTED_AI", "").lower() == "on" else None
app = create_app(":memory:", AIAssistant(_llm, AuditLog("/tmp/subsweep_ai_audit.jsonl")), hosted=True,
                 uploads=os.environ.get("SUBSWEEP_HOSTED_UPLOADS", "on").lower() != "off")
