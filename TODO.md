## /drive run trash-dash-phase2-20260618T102928 — architectural follow-ups (2026-06-19)

- `converter/converter/{roster_consumer_lowering,so_db_consumer_lowering,spawn_call_site_lowering}.py` —
  the three consumer-lowering modules share near-identical machinery (`_method_block_end` / `_locate_region`
  / `_RE_DB_METHOD`-style region location, origin-comment anchoring, 4-method canonical-body re-emit) and
  the same followup-filed limitation (a whole-region replace silently drops a hypothetical 5th public DB
  method). A shared base/helper would consolidate them. DEFERRED out of this run's blast radius: a shared
  base would change the established `roster_consumer_lowering.py` precedent's behavior (it predates this run)
  and there is no current-input impact (the real ThemeDatabase + CharacterDatabase each have exactly 4 public
  methods; no game in the corpus emits a 5th). Revisit if a future DB needs a 5th method or a 4th
  consumer-lowering shape is added.
