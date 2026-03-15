"""
Eidolon Vault — Custom Exceptions
========================
Hierarchy of exceptions for fine‑grained error handling.
"""

class EidolonVaultError(Exception):
    """Base exception for all Eidolon Vault errors."""
    pass

class ConfigurationError(EidolonVaultError):
    """Invalid or missing configuration."""
    pass

class InputError(EidolonVaultError):
    """Error in user input (file, URL, text)."""
    pass

class LLMError(EidolonVaultError):
    """Error during LLM communication."""
    pass

class GraphBuildError(EidolonVaultError):
    """Failure during knowledge graph construction."""
    pass

class SimulationError(EidolonVaultError):
    """Error during simulation run."""
    pass

class DatabaseError(EidolonVaultError):
    """SQLite database error."""
    pass
