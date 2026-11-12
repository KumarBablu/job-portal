"""
WSGI entry point for production servers (gunicorn wsgi:app).
"""
from run import app

if __name__ == "__main__":
    app.run()
