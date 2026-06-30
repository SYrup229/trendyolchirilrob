"""
Order status vocabulary.

`TrendyolStatus`: the raw status strings Trendyol returns on an order's shipment
package. We only normalise spelling — Trendyol is authoritative for what these mean.

`InvoiceState`: our internal lifecycle for whether we've issued an invoice for
the order yet. `pending` waits for the order to reach an invoiceable Trendyol
status; `invoiced` means a local invoice exists; `sent_to_oblio` means we POSTed
it (reserved for future "go-live" wiring).
"""
from enum import StrEnum


class TrendyolStatus(StrEnum):
    CREATED = "Created"
    PICKING = "Picking"
    INVOICED = "Invoiced"       # Trendyol's own field, not our state
    SHIPPED = "Shipped"
    DELIVERED = "Delivered"
    CANCELLED = "Cancelled"
    RETURNED = "Returned"
    UNSUPPLIED = "UnSupplied"
    UNDELIVERED = "UnDelivered"
    UNKNOWN = "Unknown"


# Statuses where we're confident the order has actually shipped and a fiscal
# invoice should exist. Earlier statuses (Created/Picking) might still be
# cancelled before shipping.
INVOICEABLE_TRENDYOL_STATUSES: frozenset[str] = frozenset({
    TrendyolStatus.SHIPPED.value,
    TrendyolStatus.DELIVERED.value,
})


class InvoiceState(StrEnum):
    PENDING = "pending"             # waiting for Trendyol status to be invoiceable
    INVOICED = "invoiced"           # a local invoice row exists for this order
    SENT_TO_OBLIO = "sent_to_oblio" # POSTed to Oblio (future)
