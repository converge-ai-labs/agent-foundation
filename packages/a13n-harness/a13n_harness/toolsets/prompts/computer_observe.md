<best-practices>
Inspect the intended desktop before interacting. Pointer tools use an `observation_id` from a full-screen observation and its reported `coordinate_space`. An observation binds geometry, not screen freshness or foreground focus. Never infer coordinate mode from the model name; scroll deltas retain their advertised units.

`obs-` references belong to one logical Run and one mount incarnation; only the most recent 16 observations are retained. Same-Run context compaction does not itself invalidate them, but a new Run, eviction or target replacement can. A reference remembered in a summary is not proof of validity. If unavailable or stale, capture a new image on the intended mount, reassess the GUI and use the new reference. </best-practices>
