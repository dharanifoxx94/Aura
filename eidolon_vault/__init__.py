"""Eidolon Vault — Persistent Scenario Intelligence Engine"""

__version__ = "1.4.1"   # post‑refactor
__all__ = ["EidolonVaultEngine"]

def __getattr__(name):
    if name == "EidolonVaultEngine":
        from .engine import EidolonVaultEngine
        return EidolonVaultEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
