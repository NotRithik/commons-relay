# Commons Relay

Commons Relay is a Logos Core module for autonomous agents with a shielded LEZ wallet, encrypted Logos Storage, encrypted Logos Messaging, owner-controlled spending, and A2A-compatible agent-to-agent tasks.

The model loop is replaceable. Wallet authority is not. A planner may propose a typed skill call, while deterministic code verifies signatures, schemas, spending policy, exact payment quotes, effect hashes, and network receipts before state is marked complete.

## Review status after the 8 September testnet reset

The public testnet restarted after the earlier acceptance runs. Receipts referring
to blocks around 42,557 are historical evidence, not current spendable balances or
live deployment state. Existing wallets and receipts are preserved; the wallet
rejects a network head older than its recorded checkpoint instead of showing old
funds as available. This is a rollback-height check, not universal reorg detection.

The owner UI separates **Chat**, **Activity**, and **Skills & tools**. It reads
the selected agent's live registry; the three demo agent names are independent
identities, not hardcoded task categories. In actual Basecamp UI testing, an owner
request listed stored files; a separate manual request ran an installed text
extension; and real GPT-5.6 Luna conversations invoked both the file-list tool and
that custom extension through the signed permission engine. The extension counted
3 words and 17 characters in the synthetic input `Hello Logos world`. All these
tasks had zero testnet-token spending. See `evidence/owner-chat-ui-acceptance.json`
and `evidence/custom-skill-ui-acceptance.json`. These do not replace fresh-network
payment, recovery and three-use-case acceptance.

A provider schema rejection found during the full-tool test remains recorded.
Provider strict generation is enabled only for supported schema subsets; the
complete original Relay schema and permission checks remain authoritative.
Chat requires explicit local model configuration and user data-sharing consent.
No model starts merely because Basecamp opens.

The current agent also retrieved a 49-byte synthetic file through owner chat.
The downloaded plaintext matched the original byte for byte and by SHA-256;
the signed task reported authenticated decryption. See
`evidence/current-file-vault-ui.json`. This is file-vault acceptance, not proof
that paid peer workflows or every submission criterion is complete.

The corrected standalone private-proof workflow has passed on commit `43ea9c1`.
See `evidence/local-real-proof-macos.json` for the separate local 5-unit proof.
New UI and wallet changes still need their own matching-commit CI and release
checks. A builder-narrated video, current testnet recovery evidence, and the
submission's human attestations are separate gates, not implied by passing tests.

## Previously recorded integration evidence

The pre-reset testnet build was exercised as a real Logos Core module, not only as unit-test fixtures.

- Three separate agents run in Logos Core for Storage, Messaging, and Blockchain roles. Each has its own shielded LEZ account and Messaging identity. See `evidence/three-testnet-agents.json` and `evidence/module-load.json`.
- A private paid A2A task completed on the public LEZ testnet. The client paid 3 testnet base units, the provider executed `program.query`, and the balances moved from 50 → 47 and 50 → 53. `RISC0_DEV_MODE=0` was active. See `evidence/paid-a2a-private-lez.json`.
- A one-command headless deployment created a fresh agent, wallet, owner identity, policies, module configuration, and signed Agent Card without starting inference or requesting faucet funds. See `evidence/one-command-deployment.json`.
- The encrypted owner channel works from a separate Logos Core instance. See `evidence/owner-channel-live.json`.
- The native owner UI is installed in the same Basecamp 0.2.3 instance as Commons for Logos, uses `Logos.Theme` / `Logos.Controls`, and connects as the separate `commons-relay-main` owner/controller profile while role agents stay headless. See `evidence/basecamp-ui.json`.
- An above-threshold 6-unit transfer was held for owner approval under a 5-unit limit. No wallet effect was prepared or broadcast, and the owner notification was delivered. See `evidence/above-threshold-live.json`.
- File upload/download, encrypted file-key sharing, group messaging, and a two-provider multi-agent workflow have live evidence under `evidence/`.
- The optional Pi planner adapter has also been exercised with GPT-5.6 Luna on a synthetic Storage task. Planner inference remains optional and disabled by default.

The repository contains no production wallet or owner keys. Development evidence uses disposable testnet accounts only.

## Architecture

Commons Relay has three authority layers:

1. **Planner** — optional Pi or another local/API model. It chooses among registered typed skills.
2. **Task and permission engine** — verifies owner/delegate signatures, schemas, expiries, spending thresholds, rolling budgets, and durable task state.
3. **Logos adapters** — perform Storage, Messaging, Wallet/LEZ, program, and A2A effects, then reconcile the exact recorded effect instead of trusting a planner's claim of success.

The Core plugin exposes a fixed native bridge. It is not a shell proxy and does not expose arbitrary module calls. Wallet signing material remains in the wallet profile. The agent receives the owner's public authorization key, not the owner's private key.

See `docs/ARCHITECTURE.md` and `docs/SECURITY.md`.

## Default skills

All LP-0008 default skills are registered and documented:

- Storage: `storage.upload`, `storage.download`, `storage.list`, `storage.share`
- Messaging: `messaging.send`, `messaging.join`, `messaging.create_group`
- Blockchain: `wallet.balance`, `wallet.send`, `wallet.history`, `program.query`, `program.call`, `program.deploy`
- A2A: `agent.card`, `agent.discover`, `agent.task`, `agent.subscribe`, `agent.cancel`
- Meta: `meta.skills`, `meta.status`, `meta.configure`

Third-party zero-spend skills can be added as SHA-256-pinned subprocess extensions without changing the core module. See `docs/SKILLS.md`.

## One-command headless deployment

A fresh agent can be created with `scripts/deploy-agent.py`:

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
  --sodium-library /path/to/libsodium
```

Use `--role storage`, `messaging`, or `blockchain`. The command creates new state only and refuses existing destination directories. It does not request faucet tokens and does not start model inference.

Deployment details are in `docs/DEPLOYMENT.md`.

## Owner interaction

The agent keeps an encrypted owner channel over Logos Messaging. The Basecamp owner module uses the same `Logos.Theme` and `Logos.Controls` components as the rest of Basecamp and talks to the local Core module over IPC.

Owner commands can also be created from the CLI without exposing the private signing key:

```sh
python3 scripts/owner-command.py \
  --owner-dir /path/to/owner-profile \
  task storage.list \
  --arguments '{}'
```

See `docs/OWNER.md` for the Basecamp and CLI flows.

## Tests

The deterministic Python suite covers task authorization, spending limits, concurrency, recovery, A2A lifecycle/payment binding, encrypted messaging, file encryption/sharing, extension loading, deployment tooling, and native-source contracts:

```sh
python3 -m unittest discover -s tests -v
```

The LEZ wallet companion has its own Rust suite:

```sh
cargo +1.94.0 test --locked --manifest-path wallet/Cargo.toml
```

The optional Pi adapter tests are under `tests/pi/`.

CI has two workflows:

- `core-tests.yml` runs the Python, wallet, actual QML-function tests, and clean native Linux builds on every push/PR.
- `real-local-proof.yml` is a manual clean standalone LEZ run that builds the pinned sequencer and wallet, forces `RISC0_DEV_MODE=0`, generates a private proof, submits the prepared transaction, and checks the resulting private balance.

## Reproducible real-proof demo

Prepare the pinned LEZ/prover dependencies and build the standalone components:

```sh
/bin/sh scripts/prepare-local.sh fetch
/bin/sh scripts/prepare-local.sh build
```

Then run the clean local proof demo:

```sh
python3 scripts/demo-local.py --timeout-seconds 18000 --amount 5
```

The demo creates a fresh local-only wallet and sequencer state, generates a real private proof with `RISC0_DEV_MODE=0`, confirms the exact transaction, and writes a sanitized report to `out/local/latest-local-report.json`.

## A2A over Logos Messaging

Agent Cards and task state use the A2A 1.0 data model. Logos Messaging replaces HTTP transport, while an explicit LEZ payment extension binds a task to a quote, receiver, amount, asset, expiry, and refund address. A payment message alone is never accepted as proof of payment; the provider checks the actual LEZ transaction and receiving commitment.

See:

- `docs/a2a-logos-binding-v1.md`
- `docs/a2a-payment-v1.md`

## Evidence and prize checklist

Sanitized live evidence is committed under `evidence/`. The LP-0008 criterion map is in `docs/PRIZE-CHECKLIST.md`.

Private keys, private RISC0 witness data, local wallet databases, and full daemon logs are not committed.

## Licenses

The project is dual-licensed under MIT and Apache-2.0. See `LICENSE-MIT` and `LICENSE-APACHE`.
