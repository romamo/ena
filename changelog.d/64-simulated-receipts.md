### Fixed

- A payment the stand-in bank completed shows as simulated: its receipt carries `simulated: true` and its explanation ends "(simulated: no money moved)", on the receipt card the chat streams and the one the owner's approve returns, and a duplicate trigger gets the same labelled receipt back. A bank declares whether its payments are simulated (`simulated`), one that doesn't counts as simulated, and a payment through a bank that declares itself real, a failed payment and a decline carry `simulated: false`. Receipts stored before the label load as not simulated (#64)
