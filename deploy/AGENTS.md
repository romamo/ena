# Agent operating contract

This is a [Cloudfall](https://cloudfall.tech) project: `ena-fleet`.
Humans and AI agents both edit the resources and both run the CLI. An
agent drafts and validates; a human approves anything that changes a
server. These are the terms.

## What this project manages

Ena's hosted demo: one Hetzner Cloud VM, ena.cloudfall.dev

## Invocation

Run every command as `uv run cloudfall …` (or `uv run cloudfall-engine …`)
from this directory, which the CLI recognizes as the project. From
elsewhere, pass `--project <dir>` or set `CLOUDFALL_PROJECT=<dir>`.
Relative paths, including every `tmp/` default, resolve against the
project either way. Run `uv sync` once after cloning and after any change
to the `cloudfall==<version>` pin in `pyproject.toml`.

Resources are plain YAML, one document per file, in the kind directories,
and only those directories are read as resources:

`servers/`, `baselines/`, `applications/`, `components/`, `domains/`,
`ssh-public-keys/`, `logging-stacks/`, `services/`, `alert-rules/`,
`alert-channels/`, `operator-policies/`

Write or edit them freely, then run `uv run cloudfall config validate`
before anything else. Never write a value you would have to guess: ask for
addresses, key owners, git refs, and the like.

## Commands that change nothing on servers

Run these whenever they help. They read the project and evidence, may
probe servers and public endpoints read-only, and write only under
`tmp/`.

| Command | Does |
|---|---|
| `uv run cloudfall config validate` | validate every resource against the schemas and cross-references |
| `uv run cloudfall inventory show` | show the typed, secret-free platform inventory |
| `uv run cloudfall requirements show` | show what each declared workload needs from its servers, with states |
| `uv run cloudfall operations list` | list the operations an agent may run, with their risk levels |
| `uv run cloudfall operations show` | show one declared operation with its inputs and verify step |
| `uv run cloudfall operations propose` | run one operation in check mode and record what it would change |
| `uv run cloudfall decisions list` | list what was proposed, what check mode showed, and who approved |
| `uv run cloudfall decisions show` | show one decision record: the proposal, its check-mode diff, and its outcome |
| `uv run cloudfall why` | answer why the agent did that from the record, by host, operation or time |
| `uv run cloudfall observe` | collect one read-only snapshot per server into evidence |
| `uv run cloudfall agent investigate` | let a model work an alert through the catalog: reads run, changes are proposed in check mode, nothing is approved |
| `uv run cloudfall audit` | compare the declared config with observed server snapshots |
| `uv run cloudfall services observe` | probe DNS, TLS, origin, and public routes into evidence |
| `uv run cloudfall services status` | derive each public service's lifecycle from current evidence |
| `uv run cloudfall health` | probe one component's declared health check on its servers |
| `uv run cloudfall dashboard build` | build the static read-only operations dashboard |
| `uv run cloudfall dashboard serve` | serve the read-only dashboard over HTTP (long-running) |
| `uv run cloudfall import render` | map a `render.yaml` blueprint onto config fragments under `tmp/` |
| `uv run cloudfall import render-api` | map a live Render workspace onto config fragments under `tmp/` |
| `uv run cloudfall secrets render` | render one component's secret references into a `0600` file under `tmp/` |
| `uv run cloudfall operator list` | list proposal receipts |
| `uv run cloudfall operator show` | show one proposal receipt |
| `uv run cloudfall logging open` | forward a local port over ssh to the LoggingStack's loopback Grafana and hold it until stopped (long-running); the answer names where the admin password is kept, never the password |
| `uv run cloudfall domains observe` | read one Domain's vhost, certificate and certbot.timer on its proxy server, and whether the vhost still matches its last receipt |
| `uv run cloudfall-engine inventory render` | render the Ansible JSON inventory from the validated config |
| `uv run cloudfall-engine artifact build` | clone, package, and hash one component release under `tmp/artifacts` |
| `uv run cloudfall-engine playbook list` | list the playbooks bundled with the engine |

## Commands that write project files

They write resources into this project on the controller; commit the
result. Servers are untouched.

| Command | Does |
|---|---|
| `uv run cloudfall init` | lay out a new project directory |
| `uv run cloudfall add ssh-key` | declare an `SshPublicKey` from a public key file |
| `uv run cloudfall add baseline` | declare a `Baseline` from the bundled Debian 13 baseline |
| `uv run cloudfall add server-type` | deprecated name of `add baseline`, removed in 0.9.0 |
| `uv run cloudfall add server` | declare a `Server`, creating its baseline on first use |
| `uv run cloudfall config upgrade` | rewrite the project's deprecated names (`ServerType`, `server-types/`, `serverType`) to the current ones |
| `uv run cloudfall decisions mark-interrupted` | close a `running` decision as `interrupted` once a person confirmed its run is dead (`--confirm-dead`), recording who and when; nothing on the fleet changes |
| `uv run cloudfall secrets generate` | generate the requirement secrets the store lacks, sops-encrypted |
| `uv run cloudfall secrets input` | store one requirement secret a person types, sops-encrypted |
| `uv run cloudfall operator mark-interrupted` | close a `running` proposal as `interrupted` once a person confirmed its execution is dead (`--confirm-dead`), recording who and when, so the operator may propose it again; nothing on the servers changes |
| `uv run cloudfall release approve` | record a person's approval of one built release under `decisions/releases/`; a person types the release id at a terminal, which no flag answers (`--yes` included), and nothing is deployed |

## Commands that change servers

Every command below changes servers. Do not run one, and do not add its
gate, unless a human has approved that specific run. The agent's job is to
prepare it: draft the resources, run the read-only checks, run the command
without its gate to obtain the plan, and show the plan to the human. The
human then runs the gated command, or tells the agent to run it. Nothing
on this list is safe to run on your own initiative.

| Command | Does | Gate |
|---|---|---|
| `uv run cloudfall decisions approve` | approve one recorded proposal, run it for real, and verify it | a person types the decision id at a terminal, which no flag answers (`--yes` included); off a terminal it exits 4 with `PERSON_REQUIRED`, and `decisions show` previews the proposal |
| `uv run cloudfall operator run` | watch declared alerts and drift, write proposal receipts, and run the proposals a declared `OperatorPolicy` licenses (long-running) | a human starts it; only proposals a declared `OperatorPolicy` licenses run unattended, each with a receipt |
| `uv run cloudfall deploy` | activate one built release behind its health check | `--yes`; without it the command validates and prints the plan only; a release of an application that declares `approval: manual` deploys only once a person approved it with `cloudfall release approve`, and the plan's `approval` says whether one counts |
| `uv run cloudfall rollback` | switch one component back to an existing release | `--yes`; without it the command validates and prints the plan only; it needs no approval, and for an application that declares `approval: manual` the plan's `approval` says so and why |
| `uv run cloudfall restart` | restart one component behind its health check | `--yes`; without it the command validates and prints the plan only |
| `uv run cloudfall alerts test` | send one test message through a server's alert channel | `--yes`; without it the command validates and prints the plan only |
| `uv run cloudfall baseline converge` | converge every server to its baseline, with each host's alert channel | `--yes`; without it the command validates and prints the plan only |
| `uv run cloudfall logging deploy` | deploy the declared LoggingStack after meeting its secret requirements | `--yes`; without it the command validates and prints the plan only |
| `uv run cloudfall lock clear` | remove the locks a deploy, rollback, restart or build of one component left behind, under `tmp/locks/` and on each of its servers; a lock whose process still runs on this host is never removed | a person types the component id at a terminal, which no flag answers (`--yes` included); only the locks it showed are removed |
| `uv run cloudfall data copy` | dump an external database and restore it into a declared service | `--yes`; without it the command validates and prints the plan only |
| `uv run cloudfall domains apply` | render one Domain's virtual host, and its certificate on request, on its proxy server alone; no baseline, firewall or service task runs | `--yes`; without it the command validates and prints the plan only |
| `uv run cloudfall migrate` | run the resumable end-to-end migration plan | `--yes`; without it the command validates and prints the plan only; a `deploy:<component>` step of an application that declares `approval: manual` pauses until a person runs `cloudfall release approve` for the built release |
| `uv run cloudfall backup run` | run the declared backup for one service on its server | the service must declare the backup; a receipt is written |
| `uv run cloudfall backup verify` | restore the newest backup into a scratch database on the server | the service must declare the backup; a receipt is written |
| `uv run cloudfall operator approve` | execute one proposal and verify that its trigger resolves | a person types the proposal id at a terminal, which no flag answers (`--yes` included); off a terminal it exits 4 with `PERSON_REQUIRED`, and `operator show` previews the proposal |
| `uv run cloudfall-engine playbook run` | run one bundled or project playbook against the inventory (`inspect` and `domains-observe` only read; every other playbook converges servers) | a human runs it or names the playbook to run |

There is no other way to change a server from this project: no ad hoc SSH,
no hand-written playbook run outside `cloudfall-engine playbook run`.

## Output contract

Every command prints a JSON envelope on stdout, a failure included: `ok` is
true exactly when the exit code is 0, `data` holds the result with the
command's verdict in `data.status` (`ok`, `plan`, `drift`, `unhealthy`,
`paused`, `failed`), `error` is `null` or says what failed, and `meta`
names the command and the request. Branch on the exit code first, then on
the snake_case code in `error.context.code`, never on the message. A
negative verdict such as drift exits non-zero and keeps its report in
`data`. Field names are snake_case; a record or resource inside `data`
(anything with an `apiVersion`) keeps the form it is stored in. `operator
run` and `dashboard serve` stream: one envelope line per event, then a
closing line with `meta.end`.

`uv run cloudfall --schema` prints every command with its flags, output
schema, and exit codes; `uv run cloudfall <command> --schema` prints one.
`uv run cloudfall changelog` lists what changed in that interface by
release. `--help` is for people.

`cloudfall-engine` keeps its own contract: its envelope has no `status`, a
failure is an envelope on stdout too, and its codes are listed by
`cloudfall-engine manifest` (79 to 82 for engine failures, with the
snake_case code in `error.context.code`).

## Evidence

`tmp/` holds everything derived or produced: observations, receipts,
rendered inventory, built artifacts, rendered environment files, and the
persisted migration plan. It is ignored by git. Read anything there to
answer questions about state. Exception: `tmp/env/` and any other
rendered environment file contain secret values; do not read or quote them.

Status comes from evidence, never from assumption. A deployment counts as
done when its receipt exists; a server counts as compliant when `audit`
says so against a fresh observation. Do not report success you cannot
point to a receipt or observation for.

## Secrets

Resources declare `secretRefs`; they never hold values. Secret values live
only in sops-encrypted fragments under `secrets/`, which a human writes
and commits. Never read, decrypt, write, or echo a secret value, and never
place one in a resource, a command line, a commit, or a message. If a
command needs a secret file, ask the human to provide the path.

## Git

Resources, `secrets/`, `.sops.yaml`, `pyproject.toml`, `uv.lock`, and the
documents in this directory are committed. `tmp/` and `.venv/` are not.
Commit resource changes with a message saying what changed and why; do not
commit on the human's behalf unless asked.
