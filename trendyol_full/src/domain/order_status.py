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
    ATCOLLECTIONPOINT = "AtCollectionPoint"  # parcel handed off, waiting at a pickup point/locker
    DELIVERED = "Delivered"
    CANCELLED = "Cancelled"
    RETURNED = "Returned"
    UNSUPPLIED = "UnSupplied"
    UNDELIVERED = "UnDelivered"
    UNPACKED = "UnPacked"
    UNKNOWN = "Unknown"


# Statuses where we're confident the order has actually shipped and a fiscal
# invoice should exist. Earlier statuses (Created/Picking) might still be
# cancelled before shipping.
#
# AtCollectionPoint is strictly *further* along than Shipped — the parcel has
# left the seller and is sitting at a pickup point/locker awaiting the customer
# (Trendyol shows "Factură în așteptare" for these). Since Shipped is already
# invoiceable, AtCollectionPoint must be too, otherwise locker/pickup-point
# orders never get an invoice. (UnDelivered is deliberately left out: those
# often turn into Returns, which we can't credit-note yet.)
INVOICEABLE_TRENDYOL_STATUSES: frozenset[str] = frozenset({
    TrendyolStatus.SHIPPED.value,
    TrendyolStatus.ATCOLLECTIONPOINT.value,
    TrendyolStatus.DELIVERED.value,
})


class InvoiceState(StrEnum):
    PENDING = "pending"             # waiting for Trendyol status to be invoiceable
    INVOICED = "invoiced"           # a local invoice row exists for this order
    SENT_TO_OBLIO = "sent_to_oblio" # POSTed to Oblio (future)
