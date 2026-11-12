"""
App package initialization.
Supports `gunicorn app:app` by lazily exposing the Flask app from run.py.
"""

def __getattr__(name):
    if name == "app":
        from run import app as flask_app
        return flask_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
