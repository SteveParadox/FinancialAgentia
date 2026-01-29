# exceptions.py
class OrchestratorError(Exception):
    """Base orchestrator error"""
    pass

class PhaseTimeoutError(OrchestratorError):
    """Phase exceeded timeout"""
    pass

class NonRecoverableError(OrchestratorError):
    """Cannot recover from this error"""
    pass

class TaskExecutionError(OrchestratorError):
    """Task failed"""
    pass

class DependencyError(OrchestratorError):
    """Dependency resolution failed"""
    pass

class PhaseError(OrchestratorError):
    """Generic phase error"""
    pass

class MaxIterationsError(OrchestratorError):   
    """Exceeded maximum allowed iterations"""
    pass

# validators.py
class InputValidator:
    def validate_query(self, query: str):
        if not query or not isinstance(query, str):
            raise ValueError("Query must be non-empty string")
        if len(query) > 10000:
            raise ValueError("Query too long (max 10000 chars)")
        # Check for prompt injection patterns
        suspicious = ["ignore previous", "system prompt", "you are now"]
        if any(pattern in query.lower() for pattern in suspicious):
            raise ValueError("Suspicious query pattern detected")