"""
PSIE — Custom Exceptions
========================
Hierarchy of exceptions for fine‑grained error handling.
"""

class PSIEError(Exception):
    """Base exception for all PSIE errors."""
    pass

class ConfigurationError(PSIEError):
    """Invalid or missing configuration."""
    pass

class InputError(PSIEError):
    """Error in user input (file, URL, text)."""
    pass

class LLMError(PSIEError):
    """Error during LLM communication."""
    pass

class GraphBuildError(PSIEError):
    """Failure during knowledge graph construction."""
    pass

class SimulationError(PSIEError):
    """Error during simulation run."""
    pass

class DatabaseError(PSIEError):
    """SQLite database error."""
    pass
