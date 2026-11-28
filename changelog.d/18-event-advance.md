### Added

- The event advance rule in `ena/rules/events.py`: the pattern of at least 10 completed events (deposit and cost shares, the days costs are paid before and the final payment arrives after) and a simulated short-term advance for a booked event's costs gap (costs minus deposit minus spare cash), offered when at most 1 in 10 events was cancelled, the costs are neither paid nor due yet and the final payment can repay it, or refused with the reason. `Event` gains `costs_paid_date` and `final_paid_date`, and refuses a deposit and final payment that don't add up to the price (#18)
