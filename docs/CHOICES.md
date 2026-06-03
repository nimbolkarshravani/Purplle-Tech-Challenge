# CHOICES.md

> Running notes. The full 3-decision writeup (detection model, event schema, one
> API choice) is completed in C7 after the interview. Notes below are captured as
> decisions are made so the reasoning is not lost.

## Note: billing-camera capture miss (data generation)

The synthetic event generator deliberately does NOT emit a `queue_completed`
event for every converting visitor. A configurable fraction of buyers
(`BILLING_CAPTURE_MISS_RATE`, currently 0.07) complete a purchase per the POS
data but produce no billing-queue event at all.

This models a real-world failure mode: the billing camera (CAM_BILLING_01) does
not capture every checkout. People get occluded, walk through quickly, or stand
outside the zone polygon. If the generator emitted one `queue_completed` per
basket with no exceptions, `queue_completed` would equal converting visitors
exactly (a suspiciously perfect 1:1), and downstream metrics would never see the
gap that exists in production between POS-confirmed sales and camera-detected
billing events.

Consequence for the contract: converting visitors (POS baskets) are always
greater than or equal to `queue_completed` events. Conversion is still computed
from the billing-zone correlation rule (a visitor in the billing zone within 5
minutes before a transaction timestamp counts as converted), not from a hardcoded
flag. The miss rate makes that correlation realistic rather than trivial, and it
gives the anomaly and funnel logic a real reconciliation gap to reason about.
