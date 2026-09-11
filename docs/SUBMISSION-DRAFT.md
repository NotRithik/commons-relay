# Solution: LP-0008 - Kite / Commons Relay

**Submitted by:** NotRithik

**Preparation status:** not submitted. Runtime and live acceptance evidence are recorded below. Final release/CI links and the builder-narrated video must be completed before submission; no eligibility or terms acceptance is asserted by this draft.

## Summary

Kite is a Basecamp interface for Commons Relay, a Logos Core agent runtime with its own shielded LEZ wallet, encrypted file vault, encrypted owner channel and A2A-compatible service protocol over Logos Messaging. Its optional planner chooses typed skills. Deterministic code, not the model, checks permissions, prices, spending limits and the actual transaction outcome.

The three Storage, Messaging and Blockchain deployments have separate identities and wallets. The demonstrated use cases are an encrypted file-vault round trip, a paid service marketplace, and a two-agent result aggregation. Public discovery does not require adding a provider to the owner's trusted address book. Both private and explicitly reviewed public service payments have confirmed receipts.

## Repository

- Repository: https://github.com/NotRithik/commons-relay
- License: MIT or Apache-2.0; both license texts are included.
- Final source commit: `FINAL_SOURCE_COMMIT` (fill after the candidate is frozen).
- Matching native packages: `FINAL_RELEASE_URL` (do not substitute an older RC).
- Core and real-standalone CI: `FINAL_CI_URLS`.

## Approach

### Architecture and alternatives

The planner runs separately from the task engine. Pi is the supplied optional planner adapter; other local or API models can be configured. It has no owner signing key, unrestricted shell or direct wallet authority. Model access has its own bounded cost ledger; changing a model does not change wallet identity.

The task engine validates typed inputs, owner/delegate signatures, exact reviewed intents, expiries, per-transaction limits and rolling spending reservations. SQLite journals retain the task, prepared effect and expected transaction hash. Before a side effect is dispatched, its uncertain/broadcasting state is durable. A lost network reply causes reconciliation of that recorded effect, not another transfer. Above-threshold work waits for the owner; unreachable approvals expire rather than silently executing.

The native Core bridge has a fixed operation table for Storage, Messaging and the wallet companion. It is not a general module-call or shell proxy. Owner commands arrive over a separate authenticated encrypted channel. The Basecamp owner window holds the owner's authorization key; the agent stores that key's public identity. The agent's wallet is distinct from the owner's wallet.

I rejected a chatbot wrapper with direct wallet access because language-model output cannot establish authorization or settlement after a timeout. I also rejected a central HTTP service directory: signed Agent Cards are stored in Logos Storage and advertised over a Messaging topic. Clients verify the card's public service identity without treating it as permission to issue owner commands. A self-signature proves possession of a key, not reputation or answer correctness.

The A2A 1.0 schema/lifecycle is carried by a documented Logos Messaging transport binding. An unmodified HTTP-only A2A client does not automatically speak this binding. A framework-neutral Python adapter provides discovery, task requests, streaming and cancellation through local Core IPC. There is no additional application web server between owner, client and provider.

A paid provider may install a SHA-256-pinned subprocess extension without editing Core. The operator selects the public skills and prices. The A2A engine verifies the payment before running the extension; earning a fee does not grant the extension wallet-debit authority. The customer installs neither the provider's executable nor its manifest. Extensions are trusted operator-installed programs, not an OS sandbox for hostile code.

Private payments retain autonomous execution under the owner's limits. Public mode is opt-in: it spends a separate public balance, reveals sender/recipient/amount, and needs exact owner approval. The system never unshields funds or silently switches modes. A public transaction hash alone does not authenticate its payer; a domain-separated payer signature binds the exact quote, transfer, network and payment/refund purpose, and the receiver independently checks LEZ.

### Why Logos matters

Native LEZ accounts make the agent a participant with its own funds rather than a custodial row in an application database. Logos Storage addresses and encrypted payloads support the file vault and signed-card publication. Logos Messaging supplies discovery and encrypted owner/agent transport without a new central application broker. Replacing these with a hosted database, payment custodian and HTTP relay would change the trust and availability model being demonstrated. External model/API providers remain external dependencies when the owner elects to use them; an Exa query is sent to Exa.

### What failed and what changed

A private proof taking tens of minutes previously delayed liveness; independent bounded read/I/O lanes now keep owner interaction responsive while wallet effects remain serial. A long-running inbox-capacity fault was repaired with durable cursor/history handling rather than deleting history. A copied Linux native module initially lacked its package manifest; fresh installs now generate canonical variant manifests.

A malformed public program instruction was retained as a separate historical unresolved call, not relabeled as success. The corrected default call was reviewed in Basecamp and confirmed at block 3711. A later public Exa payment completed, but chat rejected the saved authorization after the owner's approval legitimately shortened its expiry. Recovery now accepts a strictly narrower expiry while preserving exact signed inputs/price and rejecting extensions. The existing result was recovered without another search or payment.

## Success Criteria Checklist

The explanations below use the official criteria in their original order. Checkmarks mean the stated observation exists, not evaluator acceptance.

- [x] **1. The agent module loads and runs inside Logos Core alongside the wallet, storage, and messaging modules without requiring modifications to those modules.** Native module in Basecamp and fresh headless Linux deployment. Final source/assets must match. `evidence/fresh-one-command-windows-20260911.json`

- [x] **2. The agent has its own shielded LEZ account and can send and receive tokens independently of the owner's wallet.** The agent wallets are separate; private wallet.send confirmed at block 3703. `evidence/default-wallet-program-ui-20260911.json`

- [x] **3. The owner can deploy the agent and configure it with a single CLI command on any machine using Logos Core headless.** Fresh one-command Linux deployment and in-app local setup observed; dependencies must first be installed. `evidence/fresh-one-command-windows-20260911.json` `evidence/create-agent-recovery-ui-20260911.json`

- [x] **4. The owner can interact with the agent in real time from a separate Logos app instance using Logos Messaging, with no intermediary server.** Separate owner app communicates over authenticated encrypted Logos Messaging; cross-machine Exa flow is also recorded. `evidence/chat-windows-exa-verified-20260911.json`

- [x] **5. The spending threshold mechanism correctly holds above-threshold transactions for owner approval and executes below-threshold transactions autonomously.** Recorded above-threshold hold and no-owner-click private paid task. Public payments intentionally require a fresh explicit review. `evidence/current-approval-ui.json` `evidence/paid-public-extension-20260911.json`

- [x] **6. All default skills listed above are implemented and documented.** All 21 required default skills are registered and documented, with completed receipts in the current role journals. Current A2A cancellation, paid failure/refund and restart recovery are separately observed. `evidence/required-default-skills-20260911.json` `evidence/pending-restart-cancel-ui-20260911.json` `evidence/public-failed-service-refund-20260911.json`

- [x] **7. Agent-to-agent coordination is A2A-compatible: Agent Cards follow the A2A schema, task interactions follow the A2A task lifecycle, and the implementation is documented as an A2A transport binding over Logos Messaging.** Cards and lifecycle use the documented Logos Messaging transport binding. Unmodified HTTP-only A2A clients still need that binding. `evidence/a2a-stream-after-inbox-recovery.json`

- [x] **8. Two or more agents can discover each other via Agent Cards, execute a task following the A2A lifecycle, and transfer LEZ payment autonomously, without owner intervention.** Publicly discovered, non-owner-contact text-statistics provider was paid without an owner payment click. `evidence/paid-public-extension-20260911.json`

- [x] **9. At least 3 of the illustrative use cases above are demonstrated end-to-end on LEZ testnet.** File-vault round trip, paid marketplace and two-provider aggregation have end-to-end receipts. `evidence/current-three-use-cases.json`

- [x] **10. Three separate agents are deployed on LEZ testnet — one per default skill category (Storage, Messaging, and Blockchain) — each with a demonstrated, reproducible deployment and evidence provided.** Separate Storage, Messaging and Blockchain role identities and deployments are recorded. `evidence/three-current-roles-20260911.json`

- [ ] **11. Full documentation — including the skill interface spec, deployment guide, and owner interaction guide — and a clean public repository are delivered.** Current source is not yet a clean matching public release. 

- [x] **12. Provide a documented skill interface (module/SDK) that can be used to add new skills without modifying the core agent module.** Hash-pinned subprocess skill interface and installation documentation; actual third-party text-statistics service was called and paid. `evidence/paid-public-extension-20260911.json`

- [x] **13. The owner-facing interface is accessible from the Logos app (Basecamp) via the owner channel — local build instructions and loadable assets are provided.** Live Basecamp owner channel, local setup, public service review and clickable tool summaries observed. Final stable-row/scrolling patch and matching assets still need verification. `evidence/create-agent-recovery-ui-20260911.json`

- [x] **14. The agent module recovers from transient failures (network interruptions, node restarts) without losing pending task state.** The provider retained the exact pending unpaid task and quote across restart, reconnected, and accepted cancellation. Previous mailbox/identity recovery evidence also remains. This is not a claim of a mid-payment crash replay. `evidence/pending-restart-cancel-ui-20260911.json` `evidence/mailbox-recovery-20260911.json`

- [x] **15. Above-threshold transactions that fail to reach the owner for approval are not executed — the agent retries notification before timing out and reports the failure.** Verified in recorded UI/runtime observations; final release must retain it. 

- [x] **16. Skill failures are isolated: a failing skill does not crash the module or affect other concurrently running skills.** Actual independent discovery remained running while an installed JSON-formatting skill failed; event sequence and strictly contained timestamps prove overlap. Discovery completed and the controller remained live. `evidence/concurrent-failure-isolation-20260911.json`

- [x] **17. Document the compute unit (CU) cost of each on-chain operation the agent performs (token transfers, program calls, deployments) on LEZ devnet/testnet. Note: LEZ's per-transaction compute budget may change during testnet.** Operation-specific resource costs are documented with verified upstream source. This pinned LEZ API has no metered CU-used receipt; unavailable CU is explicitly distinguished from guest cycles, byte length and proof wall time. `evidence/default-wallet-program-ui-20260911.json` `evidence/public-exa-chat-20260911.json`

- [x] **18. The agent module is deployed and tested on LEZ devnet/testnet.** Current-testnet default wallet/program and paid-service receipts exist. `evidence/default-wallet-program-ui-20260911.json` `evidence/chat-windows-exa-verified-20260911.json`

- [ ] **19. End-to-end integration tests run against a LEZ sequencer (standalone mode) and are included in CI.** Real local proof passed; final-version CI integration still pending. 

- [ ] **20. CI must be green on the default branch.** No green default-branch run for the unfinished current tree. 

- [x] **21. A README documents end-to-end usage: deployment steps, agent configuration, and step-by-step instructions for deploying and interacting with the agent via CLI and the Logos app owner channel.** README contains headless deployment, role selection, owner CLI/Basecamp interaction, provider discovery, explicit public/private choice, and linked setup guides. Final source/assets publication is tracked separately in #11. 

- [x] **22. A reproducible end-to-end demo script is provided and works against a real local sequencer with `RISC0_DEV_MODE=0`.** Real standalone private proof completed using prerequisites already installed. This does not verify clean dependency installation or the later public-payment changes. `evidence/local-current-wallet-proof-20260911.json`

- [ ] **23. A recorded video demo of the end-to-end flow is included in the submission; the recording must show terminal output (including proof generation) to confirm `RISC0_DEV_MODE=0` was active.** Builder-narrated video is intentionally last. 

## FURPS Self-Assessment

### Functionality

All 21 required default skills are registered and documented, with completed records in the current three role journals. The default wallet send, program deployment and corrected program call confirmed at blocks 3703, 3639 and 3711. The autonomous private marketplace demonstration paid a publicly discovered third-party text-statistics provider at block 3206 without an owner click on that payment. A separate public Exa request discovered the provider through chat and paid one explicitly approved public unit at block 3914.

### Usability

Create Agent creates a separate unfunded local identity after review. Saved owner connections are clearly separate from Services discovery. Providers select their exported skills and prices; customers find, fill inputs and review exact price/privacy. Task rows expose recorded state and outcomes rather than asserting that a model sentence proves completion. Long saved results are read with bounded paging and a digest binding; reads do not repeat a search or payment. Remote host setup is an explicit CLI boundary rather than hidden SSH from the app.

### Reliability

The live invalid-JSON service failed after payment; the provider refunded it and the client independently verified the full refund before releasing its reservation. A subsequent free task succeeded. A separate discovery remained running while an installed formatter failed, proving that the failure did not terminate or block the independent task.

An accepted unpaid A2A task and its exact quote survived a provider restart, the owner channel reconnected, and the default cancellation skill canceled that same task without a payment. Uncertain effects retain their original identifiers. These observations do not claim escrow, an independent guarantee against a malicious provider refusing a refund, or an exhaustive production security audit.

### Performance

The public Exa task took 121 seconds including 64 seconds awaiting owner approval; post-approval task time was 57 seconds. That is not isolated chain latency. The prior cross-machine private Exa task took 2670 seconds end to end; different network paths and conditions mean this is not a controlled performance ratio.

At the pinned LEZ revision, transaction lookup exposes the transaction and block, not a metered CU-used receipt. The cost document states that CU is unavailable rather than zero, and separately reports real proof times, guest cycles, deployment bytes and confirmed transaction IDs. Public execution has a 33,554,432-cycle ceiling, not a fee schedule. No CUDA speedup is claimed.

### Supportability

The repository includes the native modules, wallet companion, CLI deployment/restart tools, third-party SDK, owner/provider/client guides, protocol bindings and sanitized observations. Dependencies and proof assets are pinned. The real standalone script forces RISC0_DEV_MODE=0 and verifies the resulting private balance; final CI runs and packages must be tied to the release source. Local UI/manual observations are separate from deterministic CI regression checks.

## Known limitations

- Testnet resets can invalidate older network state; historical receipts are not current balances. This is not audited production custody.
- Public discovery requires reachable peers on the same Logos Messaging network/topic; a published card is not proof of connectivity or service quality.
- HTTP-only A2A clients require the documented custom transport adapter. No independent TCK certification is claimed.
- External API/model use sends the selected query/context to that provider. Agent-channel encryption does not make the upstream API local or private from its operator.
- Operator-installed subprocess extensions need OS isolation if untrusted. The core provides typed/hashes/authority boundaries, not a full process sandbox.
- Refunds are verified when delivered, but the protocol is not trustless escrow enforcing a dishonest provider's cooperation.
- Private proving is slow on the demonstrated laptop; public mode changes privacy and never activates merely because a user asks for speed.

## Supporting Materials

The source repository contains the following evidence and guides:

- `evidence/required-default-skills-20260911.json`: all required skills and their recorded role/task identities.
- `evidence/current-three-use-cases.json`, `chat-vault-observed-20260911.json`, `chat-multiagent-complete-20260911.json`: three use cases and their actual inputs/results.
- `evidence/paid-public-extension-20260911.json`: public discovery with autonomous private payment.
- `evidence/chat-windows-exa-verified-20260911.json`: cross-machine private-paid service and matching outputs.
- `evidence/public-exa-chat-20260911.json`: chat discovery, explicit public payment and returned data.
- `evidence/public-failed-service-refund-20260911.json`: real failure and full client-verified refund.
- `evidence/pending-restart-cancel-ui-20260911.json`: retained pending task/quote and subsequent UI cancellation.
- `evidence/concurrent-failure-isolation-20260911.json`: real overlapping task intervals and event order.
- `evidence/local-current-wallet-proof-20260911.json`: RISC0_DEV_MODE=0 standalone proof and verified private balance.
- `docs/ARCHITECTURE.md`, `SKILLS.md`, `DEPLOYMENT.md`, `OWNER.md`, `PROVIDERS.md`, `CLIENTS.md`, `SECURITY.md`, `PERFORMANCE.md`, `a2a-logos-binding-v1.md`, `PUBLIC-PAYMENTS.md`.
- Builder-narrated video: `VIDEO_URL_REQUIRED`. It must demonstrate at least three complete use cases and show real proof terminal output, not just a silent UI recording.

Implementation and debugging were AI-assisted. The builder must narrate the actual design and demonstrate ownership/understanding; this draft is not a substitute for that requirement.

## Terms & Conditions

`OWNER_CONFIRMATION_REQUIRED_BEFORE_SUBMISSION`: the builder must confirm eligibility, rights to the code and agreement to the current program terms. This draft does not accept terms or submit a claim on their behalf.
