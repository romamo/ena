### Fixed

- `ena-boc activate` and `execute` can finish against the bocAPI sandbox: the consent URLs no longer carry `state`, which the sandbox answered with `error=badstate`, `AuthCode.from_redirect` reads the `code` with no state check (an `error=` redirect still fails with BoC's error), and the redirect URI default is `https://localhost/`, the one the sandbox app is registered with (#35)
