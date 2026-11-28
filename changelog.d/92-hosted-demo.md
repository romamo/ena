### Added

- `ena-serve --web web/dist` serves the built chat page on the same origin as the API, and `GET /health` answers 200, so a host needs no Vite dev server in front
- The hosted demo at https://ena.cloudfall.dev: one Hetzner Cloud VM declared as a Cloudfall project in `deploy/` (server, application, component, domain), deployed behind its health check, with the page open and payments simulated
