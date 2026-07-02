"""Loads config.yaml and resolves logical model roles to concrete endpoints."""

from pathlib import Path
import yaml

# The GT-code project root (the folder that contains config.yaml).
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


class Config:
    def __init__(self, data: dict):
        self.data = data
        self.providers = data.get("providers", {})
        self.models = data.get("models", {})
        self.router = data.get("router", {})
        self.agent = data.get("agent", {})
        self.memory = data.get("memory", {})
        self.web = data.get("web", {})
        self.data_dir = ROOT / data.get("data_dir", "data")

    def model_for(self, role: str) -> dict:
        """Resolve a role like 'brain' to {provider, model, base_url}."""
        m = self.models.get(role)
        if not m:
            raise KeyError(
                f"No model role '{role}' defined in config.yaml (models: "
                f"{', '.join(self.models) or 'none'})"
            )
        prov = self.providers.get(m["provider"])
        if not prov:
            raise KeyError(f"Role '{role}' points at unknown provider '{m['provider']}'")
        return {
            "role": role,
            "provider": m["provider"],
            "model": m["model"],
            "base_url": prov["base_url"].rstrip("/"),
        }

    def provider_base(self, provider: str) -> str:
        return self.providers[provider]["base_url"].rstrip("/")

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            raise FileNotFoundError(f"config.yaml not found at {path}")
        with open(path, "r", encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})
