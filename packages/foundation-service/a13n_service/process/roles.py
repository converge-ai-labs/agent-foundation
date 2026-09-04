"""Process-role ownership predicates."""

from a13n_service.settings import ServiceRole


def owns_control(role: ServiceRole) -> bool:
    return role in {ServiceRole.all, ServiceRole.control}


def owns_worker(role: ServiceRole) -> bool:
    return role in {ServiceRole.all, ServiceRole.worker}


def owns_connectivity_data(role: ServiceRole) -> bool:
    return role in {ServiceRole.all, ServiceRole.connectivity}


__all__ = ["owns_connectivity_data", "owns_control", "owns_worker"]
