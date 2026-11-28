### Fixed

- The sandbox account card no longer breaks after a few reads: the bocAPI sandbox allows only a few account reads a day without the customer (error 037), so a read is kept in the store and served for 6 hours, also across restarts, and when the sandbox refuses a fresh read the card shows the last copy, labelled "last copy, BoC sandbox" with its time
