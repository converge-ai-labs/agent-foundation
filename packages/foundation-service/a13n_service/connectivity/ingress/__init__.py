"""Ingress resources, routing, and durable admission."""

from a13n_service.connectivity.errors import NativeError

from .domain import Ingress, IngressCollection, Route, RouteCollection

__all__ = [
    "Ingress",
    "IngressCollection",
    "NativeError",
    "Route",
    "RouteCollection",
]
