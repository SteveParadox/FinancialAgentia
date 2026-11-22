"""Bounded-label metrics exported through the existing authenticated /metrics."""

from prometheus_client import Counter, Histogram

OPERATIONS = Counter(
    "financialagentia_graph_operations_total",
    "Graph transaction outcomes",
    ["mode", "outcome"],
)
DURATION = Histogram(
    "financialagentia_graph_transaction_seconds",
    "Graph transaction duration including retries",
    ["mode"],
)
