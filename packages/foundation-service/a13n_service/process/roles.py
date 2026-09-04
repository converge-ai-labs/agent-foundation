"""Process-role ownership predicates."""

from a13n_service.settings import ProcessRole


def owns_control(role: ProcessRole) -> bool:
    return role in {ProcessRole.all, ProcessRole.control}


def owns_worker(role: ProcessRole) -> bool:
    return role in {ProcessRole.all, ProcessRole.worker}


def owns_connectivity_data(role: ProcessRole) -> bool:
    return role in {ProcessRole.all, ProcessRole.connectivity}


__all__ = ["owns_connectivity_data", "owns_control", "owns_worker"]
