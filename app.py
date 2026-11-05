"""Entrypoint for a serverless host (Vercel loads the top-level `app`): the hosted, sample-data-only build of SubSweep.

Run SubSweep on your own computer (`python -m subsweep`) to analyse your own statements; that build is untouched. This one exists so the
product can be tried from a link. It takes no uploads, writes nothing to disk and shares no memory between requests (see create_app).
"""
import os

os.environ["PAYPAL_MODE"] = "mock"          # a public demo never calls PayPal, whatever else is set in the environment

from subsweep.ai import AIAssistant, AuditLog, build_default_llm  # noqa: E402
from subsweep.api.app import create_app  # noqa: E402

# Built-in explanations by default. The model is used only if the operator opts in with SUBSWEEP_HOSTED_AI=on, because a public
# endpoint would otherwise spend their API credits.
_llm = build_default_llm() if os.environ.get("SUBSWEEP_HOSTED_AI", "").lower() == "on" else None
app = create_app(":memory:", AIAssistant(_llm, AuditLog("/tmp/subsweep_ai_audit.jsonl")), hosted_demo=True)
