### Added

- The synthetic month of the owner journey in `ena/seed.py`, built from a fixed random seed as data-model entities (a €180 dairy invoice under the €500 limit, a €1,240 flour invoice above it, card sales, rent, a loan instalment and a lean week 3), and `ena/replay.py`, an accelerated clock that plays it as a time-ordered event stream through a handler in about a minute, with the clock and the sleep injected (#14)
