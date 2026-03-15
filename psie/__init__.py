"""PSIE — Persistent Scenario Intelligence Engine"""
from .engine import PSIEEngine

__version__ = "1.4.1"   # post‑refactor
__all__ = ["PSIEEngine"]
from . import cli_extension
