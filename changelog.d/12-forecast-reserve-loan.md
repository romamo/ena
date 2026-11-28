### Added

- The cash rules in `ena/rules/`: an 8-week arithmetic forecast that flags the first lean week (a closing below the next loan instalment plus one week of average outflows), a reserve offer that sets the instalment aside when the lean week comes first, and a simulated Digital Business Loan pre-fill for the gap the reserve can't cover (rounded up to €500, at most €20,000, 2 years), each offer carrying the named numbers it used (#12)
