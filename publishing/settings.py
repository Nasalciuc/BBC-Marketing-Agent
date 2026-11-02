"""Publishing settings — environment variables with the WA_ prefix (Railway variables, never a committed .env)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from publishing.browser import BrowserConfig, resolve_binary


class PublishSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WA_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    enabled: bool = False             # nothing is posted while false; dry runs, status and login still work
    targets: str = ""                 # chat names allowed to receive posts, exactly as in WhatsApp, "|"-separated
    default_target: str = ""
    url: str = "https://web.whatsapp.com/"
    binary: str = "agent-browser"
    session: str = "bbc-wa"
    profile_dir: str = ""             # persistent Chrome profile with the WhatsApp Web login (Railway volume)
    headed: bool = True               # WhatsApp Web is a desktop page; on a server agent-browser starts Xvfb
    executable_path: str = ""
    step_timeout_ms: int = 25_000
    ready_timeout_s: float = 90.0
    sent_timeout_s: float = 60.0
    evidence_dir: str = "output/whatsapp"
    selectors_file: str = ""          # optional override of publishing/selectors.json
    login_chat_id: str = ""           # Telegram chat that receives the QR code (Scaler only)

    def allowed_targets(self) -> frozenset[str]:
        return frozenset(t.strip() for t in self.targets.split("|") if t.strip())

    def browser_config(self) -> BrowserConfig:
        return BrowserConfig(binary=resolve_binary(self.binary), session=self.session, profile_dir=self.profile_dir,
                             headed=self.headed, executable_path=self.executable_path,
                             step_timeout_ms=self.step_timeout_ms)

    def problems(self, *, posting: bool) -> list[str]:
        out = []
        if not self.profile_dir:
            out.append("WA_PROFILE_DIR is not set: the WhatsApp Web login would not survive a restart")
        elif not Path(self.profile_dir).is_absolute():
            out.append("WA_PROFILE_DIR must be an absolute path")
        if posting and not self.allowed_targets():
            out.append("WA_TARGETS is empty: no chat may receive posts")
        if self.default_target and self.default_target not in self.allowed_targets():
            out.append("WA_DEFAULT_TARGET is not one of WA_TARGETS")
        return out


@lru_cache(maxsize=1)
def get_settings() -> PublishSettings:
    return PublishSettings()
