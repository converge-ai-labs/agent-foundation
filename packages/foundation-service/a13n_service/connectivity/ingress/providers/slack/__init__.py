"""Slack ingress provider."""

from .adapter import SlackIngressAdapter, SlackIngressConfig, SlackRouteMatch

__all__ = ["SlackIngressAdapter", "SlackIngressConfig", "SlackRouteMatch"]
