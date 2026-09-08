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
5. writes the 21-skill policy, contacts, owner channel, role exports, official
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
control only which services are exported to other agents; all 21 owner skills
remain installed locally.

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
