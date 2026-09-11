# Headless testnet deployment

`scripts/deploy-agent.py` creates and starts a fresh Commons Relay agent in one
command. It creates new state only: existing owner, wallet, agent or Core session
directories are never overwritten.

The command performs these steps:

1. validates the supplied `logosctl`, module directory, wallet companion and
   libsodium file;
2. creates a fresh LEZ testnet wallet without making a transaction;
3. derives the Messaging signing/encryption children from the wallet root
   identity and imports only those child keys into the agent vault;
4. creates a separate owner authorization key and owner Messaging identity;
5. writes the complete LP-0008 default-skill policy plus the zero-cost peer liveness helper, contacts, owner channel, role exports, official
   `logos.test` Storage bootstrap snapshot and Delivery configuration;
6. starts Logos Core headless with inference disabled and `RISC0_DEV_MODE=0`;
7. loads and configures `commons_relay_module`, starts the agent, publishes its
   signed Agent Card, and writes a redacted deployment receipt.

It does **not** claim faucet tokens and does **not** start a language model. A
fresh deployment therefore reports a zero shielded balance until the operator
explicitly funds it with disposable testnet units.

Example:

```sh
python3 scripts/deploy-agent.py \
  --role storage \
  --agent-root "$HOME/relay-demo/agent" \
  --owner-root "$HOME/relay-demo/owner" \
  --wallet-root "$HOME/relay-demo/wallet" \
  --session "$HOME/relay-demo/core" \
  --logosctl /path/to/logosctl \
  --modules-dir /path/to/logos/modules \
  --wallet-binary /path/to/commons-relay-wallet \
  --sodium-library /path/to/libsodium.dylib
```

Use `--role storage`, `messaging`, or `blockchain`. The bundled role templates
control only which services are exported to other agents; all LP-0008 default skills remain installed locally, together with the additional free peer-liveness check.

The deployment receipt is written to `OWNER_ROOT/deployment.json`. It records the
agent address, root NPK, owner Messaging address, policy, daemon PID and the
Core `TMPDIR`, but no private key material. Use that recorded `TMPDIR` for later
`logosctl --config-dir SESSION ...` calls so the local Core endpoint namespace is
stable across shells.

## Storage testnet bootstrap

`config/logos-storage-testnet.json` is a dated snapshot of the official
`logos.test` Storage SPR records from `logos-storage/logos-storage-nim`. The
native bridge validates every record and still binds its local API/listen sockets
to loopback. Operators can pass an updated reviewed snapshot with
`--storage-preset`.

For deterministic same-machine acceptance tests the core additionally has an
internal `storage.connect_local` control method. It can connect only to
`127.0.0.1` Storage peers and is not a planner skill. It exists so a test can
verify provider retrieval without waiting for DHT propagation; public Storage
uploads still use the official testnet bootstrap configuration.

## Owner channel handoff

The deploy command creates `OWNER_ROOT/agent-contact.json` and an owner Messaging
vault. The agent's `owner-channel.json` pins that owner's Messaging address. A
separate owner application can use the owner vault/contact material to send
signed commands over Logos Messaging. The agent has the owner's authorization
**public** key only; the owner signing key remains under the owner directory.

## Funding

Funding is intentionally not an implicit deployment side effect. For development
acceptance, create a bounded testnet funding flow that uses a single faucet claim,
then shield the agent's public balance with real proofs. Never use production
funds with this development module. The repository's Wallet Core refuses mainnet
endpoints and runs private proofs with `RISC0_DEV_MODE=0`.

## Recover a deployment that failed during Core startup

The deployer writes the restart receipt to the owner directory after creating the
agent profile and before starting Core. Its `deployment_status` progresses from
`profile-created` to `core-started` to `running`; only the final state means the
agent actually started. This receipt contains paths, public identity information
and runtime settings, not private key material.

When module loading or configuration fails, fix the runtime issue and use:

```sh
python3 scripts/restart-agent.py --deployment "$OWNER_ROOT/deployment.json" --check-only
python3 scripts/restart-agent.py --deployment "$OWNER_ROOT/deployment.json"
```

Do not run deployment again against different directories just to get past the
error: that would create a different identity. Restart handles both an already
running Core daemon and Core's explicit `not_running` status, then records the
actual daemon PID. A missing or malformed receipt is not permission to infer a
wallet path or reconstruct secret state. Preserve the partial directories for
inspection instead.

## Native-package and worker preflight

The deployer validates the manifest and native entry point for Relay, its wallet
companion, Storage and Delivery **before** creating a wallet. Raw CMake installs
now generate `manifest.json`; do not rely on a manifest left by an earlier package
installation. Canonical variants include `darwin-arm64`, `linux-amd64` and
`linux-arm64`. The package CLI accepts the older Linux architecture spellings as
aliases but writes canonical manifest entries.

A successful native `configure` response means startup was requested, not that
the Python worker has finished initializing. Deployment and restart now perform
bounded read-only status polling before `agent.start`; they do not repeatedly
configure or submit a task to test readiness.

A fresh-state one-command deployment on Ubuntu 24.04/WSL2, including a custom
extension, is recorded in `evidence/fresh-one-command-windows-20260911.json`.
It created an unfunded profile, started the native module and checked its restart
receipt without a model call or transfer. Dependencies were already installed;
this is not a claim of a clean dependency download/build or a GPU proof benchmark.
