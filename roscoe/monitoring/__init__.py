"""Monitoring — offline aggregation of audit logs into fleet metrics, alerts, and
optional exporters. Aggregation is the product; export is a thin, optional layer."""

from roscoe.monitoring.alerts import Alert, check_and_notify, evaluate_alerts
from roscoe.monitoring.dashboard import render
from roscoe.monitoring.metrics import Metrics, aggregate, load_audit
from roscoe.monitoring.notifier import (
    ConsoleNotifier,
    Notifier,
    NullNotifier,
    SlackNotifier,
    build_notifier,
)
from roscoe.monitoring.web import serve

__all__ = [
    "Metrics",
    "aggregate",
    "load_audit",
    "render",
    "serve",
    "Alert",
    "evaluate_alerts",
    "check_and_notify",
    "Notifier",
    "NullNotifier",
    "ConsoleNotifier",
    "SlackNotifier",
    "build_notifier",
]
