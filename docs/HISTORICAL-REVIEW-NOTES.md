# Historical review notes

The notes below describe earlier observations. They are not current balances or
proof that the latest source/assets passed every release gate. See the current
README and PRIZE-CHECKLIST for the current development snapshot.

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

A current two-peer workflow has also completed through owner chat: discovery,
two zero-price capability requests, and a final summary with receipt-derived
counts. See `evidence/current-multiagent-ui.json`. Short zero-spend tasks are
observed without repeating the task or model request; approval-gated and paid
tasks retain their separate safety boundaries.

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

## Chat and action review

The chat interface keeps separate drafts for each agent during the app session,
loads earlier messages, and shows complete replies on request. The selected model
and endpoint are visible beside Send. Inference settings accept an explicit
endpoint and model; credentials are not silently reused at a different endpoint.

A read-only turn can ask permission for one specific action without expanding its
original grant. The owner reviews the exact inputs, consequences and token limit
before that action is submitted. Declining creates no task. See
[`docs/OWNER.md`](docs/OWNER.md) for the review and restart behavior.

Fresh UI checks on 9 September covered an approved 49-byte synthetic upload, a
declined request, restart persistence, and two successful free peer delegations.
Each peer returned 21 capabilities, checked against both sides' task records.
See `evidence/20260909-*.json`. Public CI, Linux and release verification remain
separate checks for the submission commit. The fresh paid peer query also completed: its
three-unit payment was confirmed at block 1311, and the provider result matched
the caller artifact. See `evidence/20260909-paid-peer-demo.json`.

## Current deployment acceptance, checked 10 September 2026

Three role agents are live on the current public testnet (Storage / Messaging /
Blockchain). The settled autonomous paid task is **3 testnet units at block 1311**
(`d52c09ba…`). Do not treat older block-604 or block-42557 receipts as the current
settlement. See `docs/PRIZE-CHECKLIST.md`.

On 10 September the owner UI drove the vault, group, and zero-spend program-read
paths from Basecamp Skills: upload/download/share of a 128-byte note,
`program.query` of the Commons group at block 1898 (value 43, 3 members, threshold
2), group create from Storage and join from Messaging, and a 0 LEZ A2A
discover/ping/skills/cancel turn in Chat. `program.call` / `program.deploy` are
implemented but were not re-run (they always require owner approval and a private
proof). A later 3 LEZ payment attempt was stopped unpaid; do not resume it.

The above-limit GUI test held a 6-unit request under a 5-unit policy, then canceled
it without preparing a wallet effect (`evidence/current-approval-ui.json`).
A live successful refund has not been established. The builder-narrated video, CI
on the submission commit, and the owner's eligibility confirmation remain separate
gates.



## Superseded LP-0008 checklist (before 11 September final integration)

# LP-0008 criterion map

This file maps the September 2026 LP-0008 requirements to source and **live Basecamp evidence**. It does not replace the prize specification. Unit-test counts are not acceptance.

Current live profile: `logos/runtime/relay/roles-current/{storage,messaging,blockchain}`. Owner UI: Basecamp Commons Relay, profile **My agent** (Storage). Do not treat block 42557 or block 604 receipts as the current paid-A2A settlement.

## Functionality

| Criterion | Current evidence |
| --- | --- |
| Core module loads beside wallet, Storage, and Messaging | Live `logosctl` daemons for all three roles; `commons_relay_module` loaded from `runtime/basecamp-evaluator/modules/` |
| Independent shielded LEZ wallet | Storage wallet balance **44** at block **1881** (`wallet.balance` task `fcb75109…`). Paid A2A of **3 LEZ** at public block **1311**, tx `d52c09ba97c0e0cec56268b14ad5ba452a811372ee99ecceadf5bd38a9062a59` |
| One-command headless deployment | `scripts/deploy-agent.py`; `--dry-run` passed 10 Sep 2026 against the live logosctl/modules/wallet/sodium paths. Historical create-and-start: `evidence/one-command-deployment.json` |
| Separate owner Logos instance, no intermediary app server | Basecamp owner UI over Logos Messaging. Status copy: “Agent connected. Ready for your next message.” |
| Spending threshold | Automatic payments up to **5** units. Historical 6-unit hold then cancel with no wallet effect: `evidence/current-approval-ui.json`. Do not fire another private `wallet.send` for a demo |
| All default skills | Registry in `commons_relay/skills.py` and `docs/SKILLS.md`. Driven from Basecamp Skills on 10 Sep 2026 except `program.call` / `program.deploy` (implemented, always owner approval, private proofs 15–45 min; not re-run) and `wallet.send` (held/canceled historically; not re-run) |
| A2A-compatible coordination | `docs/a2a-logos-binding-v1.md`. 0 LEZ discover/ping/`agent.task` `meta.skills`/cancel in chat. `agent.subscribe` on a finished remote task returned honest “already finished” copy |
| Autonomous paid agent task | Block **1311**, 3 units, `program.query` on the Commons group. Receipt: `logos/receipts/team-20260909/a2a-paid-live-acceptance.json`. A later 3 LEZ attempt was **stopped unpaid** (`738cc4b5…`); do not resume |
| Three illustrative use cases | See below |
| Three deployed role agents | Storage (`lez-4ae3…`), Messaging (`lez-a9a5…`), Blockchain (`lez-f66f…`) live now |
| Public repo and docs | `NotRithik/commons-relay`, MIT + Apache-2.0 |

## Default skills driven from Basecamp on 10 Sep 2026

| Skill | Task id | Owner sentence / result |
| --- | --- | --- |
| `storage.upload` | `971b611343b24f1484575e607fb8c032` | Encrypted file stored: 128 bytes. Address `zDvZRwzkw83SGnq69SXXoEc56BNfM9WKX37UU8cfGR6NcN8Y1ugG` |
| `storage.download` | `642b3d6e094344b1b81c546cdf20084e` | Downloaded and verified: 128 bytes. Byte-identical to input |
| `storage.list` | `0837ded66d774b0c9145a293a98aba27` | 3 saved files… (before the overnight upload; vault then has 4) |
| `storage.share` | `c2227be94f294bbbb6fbd89eabfec87d` | Recipient inbox committed to Messaging Agent |
| `messaging.send` | `abdc637ecd7f494095097bb2e3e093b9` | Message delivered to the recipient |
| `messaging.inbox` | `7064349ade744ea9aa8c0632f2807a76` | No recent messages from other agents (outbound is not inbox) |
| `messaging.create_group` | `7fcf2d62df7f480d9e6c8217fd987fb2` | Group created: `group-7fcf2d62…` |
| `messaging.join` | `11dd4d3f3f96473da0c3a5810c6b14a0` | Messaging Agent joined that group |
| `wallet.balance` | `fcb75109e48d42838ab054aba9ac2324` | 44 testnet units at block 1881 |
| `wallet.history` | `bbe8e4a5cd664f14a95c038bb06211ca` | Prior same day; 9 actions / 7 confirmed |
| `program.query` | `17288b6d9f474a04a4051c1a0191afd1` | Zero-spend read at block **1898**; decoded Commons group value **43**, 3 members, threshold 2 |
| `agent.card` | `3b4a4c3f87744dcea73833989870fb2a` | Public name **My agent**; advertised `storage.list`, `meta.skills` |
| `agent.discover` / `agent.ping` / `agent.task` / `agent.cancel` | `eb50d23e…` / `5809b56b…` / `3ca67277…` / `d4fcceed…` | 0 LEZ comms in chat |
| `agent.subscribe` | `b9f62b2e33c84eb5aa046803347bdcdd` | Honest: that remote task already finished |
| `meta.skills` / `meta.status` / `meta.configure` | `235e6562…` / `35b7e95c…` / `3f165d25…` | 24 tools; balance+files; name set to My agent |

`program.call` and `program.deploy` remain implemented with a zero LEZ-transfer quote and **always owner approval**. They start a private proof. They were not re-run on 10 Sep.

## Three illustrative use cases (current network)

1. **Personal file vault** — Owner Skills: upload `sleep-session-note.txt` → download to `outputs/sleep-session-note-downloaded.txt` (byte-identical) → share with Messaging Agent. Receipts under `logos/receipts/continue-20260909/storage-*-20260910.json`.
2. **Paid skill marketplace** — Storage paid Blockchain `program.query` 3 LEZ at block **1311**. Not chat-initiated; owner-signed. Do not replay.
3. **Multi-agent workflow** — 0 LEZ discover + ping + `meta.skills` + cancel leftover unpaid remote; then create_group from Storage and join from Messaging (`group-7fcf2d62…`).

## Usability

| Criterion | Current evidence |
| --- | --- |
| Third-party skill interface | `docs/SKILLS.md`; `commons_relay/external_skills.py`; zero-spend SHA-256-pinned subprocess SDK |
| Basecamp owner interface | `native/ui/`; Chat / Activity / Skills. Manual Skills path is Review → Send. Capabilities list has **Run this tool** |

## Reliability

| Criterion | Current evidence |
| --- | --- |
| Recover pending tasks after restart | Storage + Basecamp restarts on 10 Sep kept the journal (84 recorded tasks after the overnight Skills drive). Permissions/tasks survived earlier restarts in `evidence/` |
| Failed owner delivery never becomes approval | Held 6-unit request; aborted unpaid 3 LEZ (`738cc4b5…`) did not broadcast |
| Skill failure isolation | Bounded concurrent dispatch in the native worker; a failed skill does not unload the module |

## Performance

`docs/PERFORMANCE.md` records guest RISC0 user cycles, program byte size, confirmed blocks, and real proof wall time. Public paid A2A proof: block 1311 receipt. Interactive UX for a new private proof is 15–45 minutes on this Mac.

## Supportability

| Criterion | Current state |
| --- | --- |
| Testnet deployment | Three role agents live on LEZ testnet |
| Standalone LEZ integration workflow | `.github/workflows/real-local-proof.yml` and `scripts/demo-local.py` |
| Core CI | `.github/workflows/core-tests.yml`. Must be green on the **submission commit**. Earlier greens on main do not cover uncommitted 10 Sep UI/copy changes |
| Clean local proof with `RISC0_DEV_MODE=0` | Script and workflow exist. Submission video must show terminal proof generation |
| README and deployment/owner instructions | Present. `--dry-run` on `deploy-agent.py` is the safe one-command check |
| Narrated end-to-end video | **Owner records last.** Silent screencast is not enough |

## Owner demo shot list (record after this tree is committed)

1. Terminal: `RISC0_DEV_MODE=0` visible; optional `deploy-agent.py --dry-run`.
2. Basecamp → Commons Relay → My agent → Agent online, model `gpt-5.6-luna`.
3. Chat overlay of the 0 LEZ discover/ping/skills/cancel turn.
4. Skills: upload / list / download / share; Activity sentences in plain English.
5. Skills: `program.query` of the Commons group; sentence with value 43, 3 members, threshold 2.
6. Messaging peer: join `group-7fcf2d62…`.
7. Activity: 0 awaiting approval; automatic payments up to 5.
8. Say out loud: paid 3 LEZ A2A already settled at block 1311; do not replay it.

## What is still not claimed

- A live successful A2A refund.
- Chat-initiated paid `program.query` (the settled 3 LEZ payment was Core-signed).
- `program.call` / `program.deploy` on this overnight run.
- CI green on the uncommitted 10 Sep source. Push only when the owner asks.

## Verified public-service additions, 11 September 2026

The native Services flow now discovers self-authenticating public service cards,
not just owner-pinned contacts. The free public task `23e9207a...` completed with
no payment and no provider entry in the owner's trusted address book.

The **paid installed extension** subsequently completed in the actual Services
UI. Task `ee383a53a13e43258e878116055365dd` paid one testnet unit to the discovered
text-statistics service and received its real 54-character, 9-word result. The
transaction was `3bfe42fe846ad713752c0ea804d16f0d968bebbd40720e9262b4e97aa8eecf60`,
confirmed at block 3206. The payment itself needed no owner approval click under
the configured threshold. Client and provider were both on the Mac. This is not
being represented as the Windows or Exa test. See
`evidence/paid-public-extension-20260911.json`.

A new **Linux/WSL headless deployment** then passed the normal deployment command
from separate empty state directories, with the corrected package manifests and
Qt readiness handling. The same deployment was stopped and restarted through
`scripts/restart-agent.py`; its identity was unchanged. It requested no faucet
funds, made no model calls, and sent no tokens. See
`evidence/fresh-linux-deployment-20260911.json`. That check uses a private LAN mesh,
not proof of unrestricted public-internet connectivity.

The **long-running inbox capacity defect** was reproduced at 10,000 retained
messages. Acknowledgements and processed control messages now move to an archive
rather than consuming active queue capacity forever. The observed migration
preserved distinct IDs, sequence numbers, and the earlier completed free and paid
tasks; the actual Basecamp owner chat reconnected. See
`evidence/mailbox-recovery-20260911.json`.

The Windows Exa provider was discovered by an actual chat request. A later chat
request asked for discovery, a capped one-unit search, and a summary. Its exact
action was approved and task `c248909274dc4882a3011cd9978efe04` started preparing a
real Mac proof. **That task is in progress, not completed evidence yet.** Do not
replay it for a screenshot or label a quote/proof start as a completed search.

`docs/PROVIDERS.md`, `docs/CLIENTS.md`, `docs/NETWORKING.md` and the updated security
model explain public discovery, paid extensions, separate owner authority and
upstream API disclosure. The optional LangChain pipeline is written but remains
unverified until its actual framework run completes.

### Remaining release gates

- Complete and inspect the current chat-driven Windows Exa task and its returned
  result. Keep payment, upstream execution and model summary as separate facts.
- Finish any missing default-skill/UI and failure/recovery acceptance checks;
  source implementation alone does not establish a completed flow.
- Package the exact final tree, verify it in Basecamp and from clean deployment
  inputs, and publish matching source/assets only after readiness review.
- Run the required CI and real standalone proof workflow on the submission commit.
  The last observed public green runs were on `3d24f952...` on 9 September, not
  this uncommitted working tree.
- Record the builder-narrated video last, including the actual proof terminal and
  clear labels for historical receipts or cuts during long proof generation.

GPU acceleration is not a release requirement and is not currently claimed. The
CUDA build attempt did not produce a validated usable prover. The completed
paid extension used the Mac's real proof path.

### Fresh two-agent aggregation and default-skill inventory

Goal `chat-ccf6cf8f888315cffed04168` completed both requested zero-priced remote
services and combined their results in the actual chat. The provider/client
artifacts matched for Storage's catalogue and Text statistics' capabilities. See
`evidence/chat-multiagent-complete-20260911.json`. The preceding four-turn-limited
attempt did not complete both services and is not the evidence used here.

A read-only inventory of the current role journals found no completed default
`wallet.send`, `program.call` or `program.deploy` task. Those remain acceptance
gaps, even though the marketplace has already exercised a private transfer.
Do not mark the entire default-skill list verified based on registry presence.

The small program-call example is being prepared as a zero-effect public program
for deploy/call acceptance. Its source alone is not a successful deployment.
