### Fixed

- The event advance refuses an ineligible event (a completed one, say) before it reads the forecast, so on 1 November, when the forecast has no history yet, the refusal is given instead of the forecast's error (#18)
- A loan or event advance offered again at another amount gets its own receipt: an offer's id now carries its amount (`loan-<week>-<cents>`, `advance-<event>-<cents>`), so `list_receipts` never shows an amount the offer card no longer does (#18)
- The seed's docstring says the demo's advance on the lean Monday is €2,800, since the forecast leaves no spare cash, not the build plan example's €1,800 (#18)
