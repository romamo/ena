### Added

- CI: `.github/workflows/ci.yml` runs one `test` job on every pull request and push to `main` (`uv sync --locked`, ruff check and format, mypy strict, pytest), so the gate merges on green tests (#4)
