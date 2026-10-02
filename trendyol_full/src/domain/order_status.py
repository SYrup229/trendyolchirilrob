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
# left the seller and is sitting at a pickup point/locker awaiting the customer.
# UnDelivered (courier could not deliver) is invoiceable too: the goods shipped,
# and Trendyol's billing view shows "Factură în așteptare" for these packages —
# verified live on order 11359838794. If one later becomes a Return, the storno
# is handled in Oblio like any other return.
INVOICEABLE_TRENDYOL_STATUSES: frozenset[str] = frozenset({
    TrendyolStatus.SHIPPED.value,
    TrendyolStatus.ATCOLLECTIONPOINT.value,
    TrendyolStatus.DELIVERED.value,
    TrendyolStatus.UNDELIVERED.value,
    # Returned: the goods shipped (taxable event) even if they came back —
    # user invoices these too and handles the storno in Oblio.
    TrendyolStatus.RETURNED.value,
})


def is_invoiceable_status(status: str, all_statuses: bool = False) -> bool:
    """
    Whether an order in `status` gets a draft invoice and shows in the Invoices
    tab. Default: only INVOICEABLE_TRENDYOL_STATUSES. With Settings → "Facturează
    comenzile cu orice status" on (`all_statuses`), every status qualifies except
    Cancelled — including pre-shipment ones and any status we don't know yet.
    """
    if all_statuses:
        return status != TrendyolStatus.CANCELLED.value
    return status in INVOICEABLE_TRENDYOL_STATUSES


class InvoiceState(StrEnum):
    PENDING = "pending"             # waiting for Trendyol status to be invoiceable
    INVOICED = "invoiced"           # a local invoice row exists for this order
    SENT_TO_OBLIO = "sent_to_oblio" # POSTed to Oblio (future)
