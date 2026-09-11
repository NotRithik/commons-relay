# Kite for Logos

## [Watch the builder demo - LP-0008 (10:46)](https://youtu.be/5tW55lGR-_Y)

[![Watch Kite on YouTube](https://i.ytimg.com/vi/5tW55lGR-_Y/hqdefault.jpg)](https://youtu.be/5tW55lGR-_Y)

**[Submission write-up and chapter links](docs/SUBMISSION.md)** | **[Release and installation assets](https://github.com/NotRithik/commons-relay/releases/tag/v0.1.0-rc.4)**


Kite is the Basecamp interface for **Commons Relay**, a Logos Core agent module.
Your agent has its own shielded LEZ wallet, encrypted file vault and Messaging
identity. It can do work for you or hire a service offered by another agent.
You choose its permissions and spending limits.

Use **Chat** to give it a goal, **Services** to find or offer help, and **Activity**
to see what actually happened. A model's claim is not a payment receipt. The task
engine checks signatures, exact prices, limits and network results before it
reports completion. Models are replaceable; wallet authority is not.

Existing module IDs remain `commons_relay_module`, `commons_relay_wallet` and
`commons_relay_owner_ui`. Renaming the interface does not change wallet identity.
This is testnet software, not audited production custody.

## Start here

- [Deploy a headless agent](docs/DEPLOYMENT.md), then [use Chat and the owner interface](docs/OWNER.md).
- [Build and publish your own service](docs/PROVIDERS.md), or [call one from another application](docs/CLIENTS.md).
- [Build the native Basecamp modules](docs/NATIVE-BUILD.md); [connect different computers](docs/NETWORKING.md).

No application web server is required between the owner and agent. Provider and
client still need a working Logos Messaging network connection: using the same
topic name does not connect otherwise disconnected nodes.

## Create, connect, or discover

**Agents you control** is the list of saved owner connections, not a public
marketplace. In a configured Basecamp installation, **Create an agent** starts a
new local, unfunded agent after an explicit review; **Connect to this agent** then
opens that new owner connection. Model inference and public service listing both
start off. See [local setup and same-identity recovery](docs/CREATE-AGENT.md).
A remote host is still deployed with the CLI, not by silently opening SSH access
from the owner window.

**Services > Find a service** discovers providers on the selected Messaging
network. **Offer a service** explicitly selects public skills and prices for an
agent you control. Discovery does not give you ownership of somebody else's
agent or copy their wallet into your owner dropdown.

## Choose payment privacy

Private payments are the default for existing callers. In the new Services flow,
a provider may explicitly also accept **public** payments. The review shows the
choice and its privacy consequence before signing. Public funds are separate;
private money is never converted automatically. Public payments avoid a private
proof, but they still need chain confirmation. The chat planner asks for a mode
before a new paid action when one was not specified. See
[public/private payment behavior and receipt verification](docs/PUBLIC-PAYMENTS.md).
The new public flow has its own evidence: a chat-discovered Exa search completed
with a one-unit public payment at block 3914, and a deliberately invalid service
request received a full client-verified public refund. See
[public search](evidence/public-exa-chat-20260911.json) and
[failed-service refund](evidence/public-failed-service-refund-20260911.json).
These are separate from the older autonomous private-payment demonstration.

## What public discovery means

A provider explicitly chooses its public services and prices. Its signed Agent
Card is stored on Logos Storage and announced on a discovery topic. A client
on that network can verify and discover the card without first adding the
provider to an owner-trusted address book.

The service channel is separate from owner authority. Discovering a provider
never gives it permission to read private files, issue owner commands or spend
unlimited funds. A signature establishes key possession, not reputation or answer
correctness. See [the security model](docs/SECURITY.md).

Custom behavior is an operator-installed executable with a manifest, schemas and
a pinned hash. The core checks a customer's payment before running a paid service;
the extension does not thereby gain access to the provider's wallet. An external
API such as Exa receives the query sent to it, even though the agent-to-agent hop
is encrypted.

## Current development evidence

The actual Basecamp Services flow has completed a **one-unit private payment to
a discovered third-party text-statistics extension**, with no provider entry in
the client's owner-trusted contacts and no owner click on the payment itself.
The returned result was 9 words and 54 characters; the transaction was confirmed
at testnet block 3206. Client and provider were both on the Mac. See
[`paid-public-extension-20260911.json`](evidence/paid-public-extension-20260911.json).

A separate fresh **Linux/WSL headless deployment and same-identity restart** passed
with no faucet requests, model calls or transfers. See
[`fresh-linux-deployment-20260911.json`](evidence/fresh-linux-deployment-20260911.json).
An actual long-running inbox-capacity fault was also repaired without discarding
message history or completed task/payment records; the Basecamp owner channel
reconnected. See [`mailbox-recovery-20260911.json`](evidence/mailbox-recovery-20260911.json).

The file-vault, above-threshold review and earlier multi-agent UI evidence remain
in `evidence/`. Their exact scope and dates matter. Older testnet receipts are not
current spendable balances, and a testnet reset can invalidate recorded network
state. [Historical review notes](docs/HISTORICAL-REVIEW-NOTES.md) preserve that
context rather than presenting old observations as a fresh result.

The chat-driven **Windows Exa** request completed with a private one-unit payment
at block 3359. Its returned text was independently matched on the Mac and Windows
provider; the saved response contains two linked excerpts and explicitly reports
provider truncation. This flow used an exact-action approval. See
[the cross-machine receipt](evidence/chat-windows-exa-verified-20260911.json).

The required `wallet.send`, `program.deploy` and corrected `program.call` flows
also completed through Basecamp, at blocks 3703, 3639 and 3711 respectively. See
[their separate receipts](evidence/default-wallet-program-ui-20260911.json).
The earlier malformed call is retained separately and is not labeled successful.

The builder video and current submission package are now linked above. See
[SUBMISSION.md](docs/SUBMISSION.md) for the exact evidence boundaries, including
video coverage, the consumed-CU reporting limitation and ancestor real-proof CI.
This is not a claim of prize acceptance or an audited production release.

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

Third-party skills can be added as SHA-256-pinned subprocess extensions without changing the core module. They may earn a listed service fee while retaining zero wallet-debit authority. See `docs/SKILLS.md` and `docs/PROVIDERS.md`.

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

## Custom public services (Kite)

The Basecamp interface now includes **Services** for finding a provider, reviewing
its inputs and exact price, or publishing a listing for your own installed skill.
Public service discovery is separate from owner-trusted contacts. It does not
grant strangers owner commands or wallet control.

Start with [the provider guide](docs/PROVIDERS.md), [the Python client adapter](docs/CLIENTS.md)
and [network setup](docs/NETWORKING.md). `scripts/install-skill.py` installs a
reviewed executable and manifest; `scripts/deploy-agent.py --service-manifest ...`
can install a custom service during a new deployment. A paid service fee is
separate from the extension's zero-spend wallet authority.

Live free public-identity discovery and task completion were observed on
10 September 2026. That is not, by itself, evidence of a paid third-party service,
a Windows/WSL deployment or a live Exa call. Those require their own receipts.
