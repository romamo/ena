# ena-fleet

A [Cloudfall][cloudfall] project: the fleet, the applications that run on
it, and the declarations that operate them, in one private repository. Every
`cloudfall` command run from inside this directory finds it on its own; from
elsewhere, pass `--project` or set `CLOUDFALL_PROJECT`.

## About

Ena's hosted demo: one Hetzner Cloud VM, ena.cloudfall.dev

## Layout

| Directory | Resource kind |
|---|---|
| `servers/` | `Server` |
| `baselines/` | `Baseline` |
| `applications/` | `Application` |
| `components/` | `Component` |
| `domains/` | `Domain` |
| `ssh-public-keys/` | `SshPublicKey` |
| `logging-stacks/` | `LoggingStack` |
| `services/` | `Service` |
| `alert-rules/` | `AlertRule` |
| `alert-channels/` | `AlertChannel` |
| `operator-policies/` | `OperatorPolicy` |

Only these directories are read as resources, so playbooks, roles, docs, and
tooling files may live anywhere else in the project. `tmp/` holds evidence,
receipts, rendered inventory, built artifacts, and rendered environment files
and is ignored by git. Secret values never enter the resources: declare
`secretRefs` and keep sops-encrypted fragments in `secrets/`, which
`.sops.yaml` encrypts to your age key once you put the key there (see the
[secrets guide][secrets-guide]).

## Start

This directory lies inside an existing git repository, so commit
it there. Install Cloudfall and validate:

```console
uv sync
uv run cloudfall config validate
```

Validation fails until the project declares its first resources. Declare
your SSH key and your first server; the server's `Baseline` is created from
the bundled Debian 13 baseline when it does not exist yet:

```console
uv run cloudfall add ssh-key ~/.ssh/id_ed25519.pub --owner <you>
uv run cloudfall add server h1 --address <ip-or-hostname>
uv run cloudfall config validate
```

Edit the written files freely; the [reference set][examples] in the
Cloudfall repository shows every kind. Then bring the host under management
and prove it:

```console
uv run cloudfall-engine inventory render --output tmp/ansible-inventory.json
uv run cloudfall-engine playbook run inspect --inventory tmp/ansible-inventory.json \
  --extra-vars cloudfall_inspect_output_directory=$PWD/tmp/observed
uv run cloudfall audit --observed tmp/observed
```

Add applications by hand or with `cloudfall import render`, then drive the
whole migration with the resumable plan:

```console
uv run cloudfall migrate --build <component>=main
uv run cloudfall migrate --build <component>=main --yes
```

Cloudfall is pinned to one released version in `pyproject.toml`; bump it
and run `uv sync` to move the project to a newer release. The links below
point at the pinned release.

[cloudfall]: https://github.com/cloudfall-dev/cloudfall
[secrets-guide]: https://github.com/cloudfall-dev/cloudfall/blob/v0.8.0rc34/docs/secrets-guide.md
[examples]: https://github.com/cloudfall-dev/cloudfall/tree/v0.8.0rc34/config/examples
