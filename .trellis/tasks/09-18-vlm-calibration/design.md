# Design

Keep the current detector/VLM boundary and conservative fusion policy. Add
versioned evidence fields rather than changing the meaning of historical
records. Threshold analysis and calibration are offline, validation-only
artifacts; production defaults remain conservative until a human-labeled set
authorizes promotion.

The first implementation slice will deliver the contracts, deterministic
gates, and calibration tooling. A fresh VLM call on a small calibration set
will only happen after the manifest is manually labeled; it is not part of the
zero-API cached replay.
