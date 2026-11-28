# ena-fleet

A [Cloudfall][cloudfall] project for Ena's hosted demo at https://ena.cloudfall.dev: one Hetzner Cloud VM, the `ena` application on it, and its public domain. Run every command from this directory (or pass `--project deploy`); `AGENTS.md` is the operating contract.

The page is open: anyone with the address can chat and approve payments, and every question spends the Anthropic key on the server. Payments are simulated (the stand-in bank completes each one and no money moves).

| Resource | What |
|---|---|
| `servers/ena-1.yaml` | Hetzner Cloud cx23 `178096211` in nbg1, Debian 13, created 2026-11-28 in the `cloudfall` hcloud project |
| `baselines/debian-application.yaml` | The bundled Debian 13 baseline: key-only SSH, unattended upgrades, nftables allowing 22, 80 and 443 |
| `applications/ena.yaml` | The `ena` service account; every release needs a person's approval (`approval: manual`) |
| `components/ena.yaml` | `ena-serve` on `127.0.0.1:8790` with the built chat page (`--web web/dist`) and the store in `/srv/apps/ena/web/shared/ena.db`, health gate `/health` |
| `domains/ena-cloudfall-dev.yaml` | nginx on ena-1 with a Let's Encrypt certificate, Cloudflare proxied |
| `decisions/releases/` | The release approvals, one per deployed release |

Cloudfall is pinned to 0.8.0rc34 for its frontend build steps (`npm ci` and `npm run build` on the controller); the project runs on Python 3.14 because ansible-core fails on 3.15.

## Deploy a new release

Needs Node.js 22 or newer and npm on the controller, root SSH to ena-1 with the `roman` key, and `ANTHROPIC_API_KEY` for the server.

```bash
uv sync
uv run cloudfall-engine inventory render --output tmp/ansible-inventory.json
uv run cloudfall-engine artifact build ena --ref <commit pushed to romamo/ena>
```

A person approves the release by typing its id at a real terminal (a `!` command in a chat has no input); the prompt waits 60 seconds:

```bash
uv run cloudfall release approve ena --release <release>
```

Write the key to a private env file under `tmp/` (never committed), then deploy behind the health check, which rolls back on its own when the check fails:

```bash
umask 077 && printf 'ANTHROPIC_API_KEY=%s\n' "$ANTHROPIC_API_KEY" > tmp/env/ena.env
uv run cloudfall deploy ena --release <release> --env-file tmp/env/ena.env --yes
```

`cloudfall rollback ena` switches back to an earlier release kept on the server.

## How ena-1 was set up

On 2026-11-28, in this order: `playbook run bootstrap --limit ena-1` (packages, the `ena` user and its folders), `playbook run baseline --limit ena-1` (after a `--dry-run`), the `ena` A record in the cloudfall.dev zone (DNS only), `playbook run domains --limit ena-1 --extra-vars '{"cloudfall_nginx_site_issue_certificate": true}'` (nginx and the certificate), the first deploy, then the record switched to proxied.

## Take it down

After the hackathon, delete the VM and the record, then this folder:

```bash
hcloud server delete ena-1
```

```bash
cf dns records delete 1953414af3c85a1c9b0c6367ee789b82 -z cloudfall.dev
```

[cloudfall]: https://github.com/cloudfall-dev/cloudfall
