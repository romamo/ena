### Added

- The owner takes an event advance with Accept or Decline on its offer card (`POST /offers/{id}/accept` and `/decline`, never a tool). Accepting writes the owner's receipt that the advance is credited (simulated: no lending API, no money moved); an offer is decided once, and an event has at most one accepted advance (#18)
- The repayment through the gate (`ena/advances.py`): when the event's final payment posts to the statement (money in, the final payment's amount, its reference naming the event), a replay handler asks the gate to repay the accepted advance (`repay-<offer id>`), paid within the limit and approved by the owner above it, once. A simulated advance is never repaid through a bank that moves real money (#18)
