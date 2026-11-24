"""Local-first KarıncaAI Studio backend package."""

def create_app(*args, **kwargs):
    # Keep launcher diagnostics available even when optional runtime deps are absent.
    from .app import create_app as factory
    return factory(*args, **kwargs)

__all__ = ["create_app"]
