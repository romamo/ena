### Added

- `ena-serve` starts the API on `127.0.0.1:8000` with the synthetic month as of `--day` (16 November by default), the gate, the JCC callback and the chat agent, from `.env`; without an Anthropic key `/chat` answers with an error event, and without JCC settings the callback refuses every call (#58)
