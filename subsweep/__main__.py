"""Start SubSweep:  python -m subsweep   (then open http://127.0.0.1:8000)

Options via environment: PORT (default 8000), HOST (default 127.0.0.1), PAYPAL_MODE=mock for the bundled PayPal demo data,
ANTHROPIC_API_KEY for AI explanations (the app works without it using built-in templates).
"""
import os

import uvicorn

from subsweep.paypal.config import load_dotenv


def main() -> None:
    load_dotenv()
    from subsweep.api.app import create_app

    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8000"))
    print(f"\n  SubSweep is running at  http://{host}:{port}\n  (Ctrl+C to stop)\n")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
