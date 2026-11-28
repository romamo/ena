# ena-fleet

A [Cloudfall][cloudfall] project for Ena's hosted demo at https://ena.cloudfall.dev: one Hetzner Cloud VM, the `ena` application on it, and its public domain. Run every command from this directory (or pass `--project deploy`); `AGENTS.md` is the operating contract.

The page is open: anyone with the address can chat and approve payments, and every question spends the Anthropic key on the server. Payments are simulated (the stand-in bank completes each one and no money moves).

All visitors share one store, `/srv/apps/ena/web/shared/ena.db`: one visitor's approved payments and chat history are what the next visitor sees, and nothing resets it between them. To start over from the synthetic month, a person stops the service, removes the store, and starts it again (`ena-serve` creates an empty store on start, and the month comes from the seed). Cloudfall has no stop command, so this is the one step run over SSH by hand, and only by a person:

```bash
ssh root@116.203.140.114 'systemctl stop ena && rm /srv/apps/ena/web/shared/ena.db && systemctl start ena'
```

| Resource | What |
|---|---|
| `servers/ena-1.yaml` | Hetzner Cloud cx23 `178096211` in nbg1, Debian 13, created 2026-11-28 in the `cloudfall` hcloud project |
| `baselines/debian-application.yaml` | The bundled Debian 13 baseline: key-only SSH, unattended upgrades, nftables allowing 22, 80 and 443 |
| `firewall/cloudflare-origin.json` | The baseline firewall with 80 and 443 restricted to Cloudflare's ranges, passed to the baseline playbook as extra vars |
| `applications/ena.yaml` | The `ena` service account; every release needs a person's approval (`approval: manual`) |
| `components/ena.yaml` | `ena-serve` on `127.0.0.1:8790` with the built chat page (`--web web/dist`) and the store in `/srv/apps/ena/web/shared/ena.db`, health gate `/health` |
| `domains/ena-cloudfall-dev.yaml` | nginx on ena-1 with a Let's Encrypt certificate, Cloudflare proxied |
| `decisions/releases/` | The release approvals, one per deployed release |

Cloudfall is pinned to 0.8.0rc34 for its frontend build steps (`npm ci` and `npm run build` on the controller); the project runs on Python 3.14 because ansible-core fails on 3.15.

## Deploy a new release

Needs Node.js 22 or newer and npm on the controller, root SSH to ena-1 with the `roman` key, and the repository's `.env` with the Anthropic key and the Jinius client.

```bash
uv sync
uv run cloudfall-engine inventory render --output tmp/ansible-inventory.json
uv run cloudfall-engine artifact build ena --ref <commit pushed to romamo/ena>
```

A person approves the release by typing its id at a real terminal (a `!` command in a chat has no input); the prompt waits 60 seconds:

```bash
uv run cloudfall release approve ena --release <release>
```

Write the server's secrets to a private env file under `tmp/` (never committed): the Anthropic key, and the Jinius sandbox buyer client so the dashboard's Jinius tab reads invoices live (without it the tab answers 503). Deploy behind the health check, which rolls back on its own when the check fails, then delete the file:

```bash
umask 077 && grep -E '^(ANTHROPIC_API_KEY|JINIUS_CLIENT_ID|JINIUS_CLIENT_SECRET)=' ../.env > tmp/env/ena.env
uv run cloudfall deploy ena --release <release> --env-file tmp/env/ena.env --yes
rm tmp/env/ena.env
```

`cloudfall rollback ena` switches back to an earlier release kept on the server.

## Answer only through Cloudflare

The baseline opens 80 and 443 to every address, so a visitor who knows the origin IP could skip Cloudflare. `firewall/cloudflare-origin.json` restricts both ports to [Cloudflare's published ranges](https://www.cloudflare.com/ips/) and keeps 22 open (the engine refuses a ruleset that restricts the SSH control port). Cloudfall 0.8's `Baseline` schema has no field for source ranges, though its firewall role takes them, so the file overrides the inventory's `cloudfall_firewall` as extra vars rather than living in `baselines/`. Check, then (once a person approves) apply:

```bash
uv run cloudfall-engine inventory render --output tmp/ansible-inventory.json
uv run cloudfall-engine playbook run baseline --inventory tmp/ansible-inventory.json --limit ena-1 --extra-vars @firewall/cloudflare-origin.json --dry-run --diff
uv run cloudfall-engine playbook run baseline --inventory tmp/ansible-inventory.json --limit ena-1 --extra-vars @firewall/cloudflare-origin.json
```

Every later baseline run must pass the same `--extra-vars`, or it reopens 80 and 443 to everyone. When Cloudflare changes its ranges, regenerate the file from https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6.

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
