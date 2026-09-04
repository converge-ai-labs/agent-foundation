"""Slack ingress provider."""

from .adapter import SlackAccountConfig, SlackIngressAdapter, SlackRouteMatch

__all__ = ["SlackAccountConfig", "SlackIngressAdapter", "SlackRouteMatch"]
