"""Runtime configuration, read once from the environment (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    paypal_client_id: str
    paypal_client_secret: str
    paypal_base_url: str
    paypal_webhook_id: str
    anthropic_model: str
    anthropic_effort: str
    use_fallbacks: bool
    data_dir: Path

    @property
    def paypal_live(self) -> bool:
        """True when real sandbox credentials are configured; otherwise the mock sandbox is used."""
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def ai_enabled(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def load_settings() -> Settings:
    mode = os.getenv("PAYPAL_MODE", "auto").lower()
    cid = os.getenv("PAYPAL_CLIENT_ID", "")
    secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
    if mode == "mock":
        cid, secret = "", ""
    return Settings(
        paypal_client_id=cid,
        paypal_client_secret=secret,
        paypal_base_url=os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"),
        paypal_webhook_id=os.getenv("PAYPAL_WEBHOOK_ID", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"),
        anthropic_effort=os.getenv("ANTHROPIC_EFFORT", "medium"),
        use_fallbacks=os.getenv("ANTHROPIC_FALLBACKS", "1") != "0",
        data_dir=Path(os.getenv("DATA_DIR", ROOT / "data" / "runtime")),
    )


settings = load_settings()
