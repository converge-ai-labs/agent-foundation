"""ASGI entry point for Foundation Service."""

from a13n_service.app import create_app

app = create_app()
