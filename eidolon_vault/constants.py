"""
Eidolon Vault — Shared Constants
=======================
Centralised constants used across multiple modules.
"""

# Allowed scenario types for validation (must match engine expectations)
ALLOWED_SCENARIO_TYPES = {
    "job_hunt",
    "business_decision",
    "negotiation",
    "relationship",
    "general",
}

# Default LLM request timeout (seconds)
DEFAULT_LLM_TIMEOUT = 60

# Allowed entity types for graph nodes
ALLOWED_ENTITY_TYPES = {"PERSON", "ORG", "ROLE", "CONCEPT", "EVENT", "UNKNOWN"}
