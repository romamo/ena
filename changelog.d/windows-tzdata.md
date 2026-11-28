### Fixed

- Windows: the project depends on `tzdata` there, so `Europe/Nicosia` resolves and `uv run pytest` no longer stops on `ZoneInfoNotFoundError`; Windows has no system time zone database for `zoneinfo` to read
