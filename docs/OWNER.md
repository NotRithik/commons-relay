# Owner interaction

Commons Relay separates the owner authorization key from the agent wallet and agent Messaging identity. The agent stores the owner's public authorization key. The owner directory holds the corresponding private key and should remain private.

## Basecamp

Relay is a control panel for agent instances. The Storage, Messaging and Blockchain
names identify three independently deployed demo agents required by the prize.
They use the same extensible registry; one instance can choose among all of its
permitted tools. They are not fixed-response bots or mandatory task categories.

Choose an instance, then wait for an authenticated owner-channel reply. Opening
the app proves neither an agent connection nor a completed task.

**Chat** accepts ordinary language. Read-only is the default. The selected model
and provider are shown beside the composer. Pressing Send uses that configuration
for the message, recent conversation and requested tool results. Private signing
keys are not sent. A configuration change invalidates an open Send review.

To allow file changes, messages or a payment, enable actions and review the goal
and maximum test-token spend. The local owner signer creates a bounded grant;
the remote planner gets only delegated authority. Per-task limits still apply.
A missing model configuration disables Send. Suggestions only fill the composer.

A read-only turn can also ask for one action when it needs permission. The request
appears in the conversation without executing a task. Open **Review requested
action** to see the exact tool, recipient or path, inputs, implications and token
limit. Approve authorizes only that action; Decline runs nothing. An existing
spending limit can still require a separate transaction approval. The original
read-only grant is not expanded.

**Activity** shows the task engine's receipts and uncertainty states, not a model's
claim of success. Open a task for its exact arguments and recorded result. Above-
threshold approval signs that one intent, policy version and expiry. A changed
agent invalidates an open review. Incomplete argument previews cannot be approved.

**Skills & tools** lists the selected agent's registered capabilities. Manual forms
are generated from their schemas, including custom skills. Use these forms to
exercise the engine without a language-model charge. Numbers, arrays and structured
inputs are validated before the review, and are validated again by the agent.

The ordinary flow does not require copying signed JSON. Private signing keys stay
in owner-only local files and never enter QML or an API prompt. The native UI uses
`Logos.Theme` and `Logos.Controls`; it is installed beside Commons for Logos in the
same Basecamp instance. The in-app **How to use Relay** guide explains these steps.

A testnet reset can invalidate historical wallets and receipts. An unavailable
balance is reported as unknown, not zero. The `meta.status` tool observes wallet
balance, bounded active-task state and local encrypted-file-reference usage.
Storage usage is not a claim about network capacity or provider retention.

## CLI owner commands

`scripts/owner-command.py` creates a signed command from an owner directory. It prints the signed envelope, not the private signing key.

Example read task:

```sh
python3 scripts/owner-command.py \
  --owner-dir /path/to/owner-profile \
  --expires-in 300 \
  task storage.list \
  --arguments '{}'
```

Send the resulting JSON through the local Core module with `scripts/control.py`:

```sh
python3 scripts/control.py \
  --logosctl /path/to/logosctl \
  --session /path/to/core-session \
  --command-file /path/to/signed-command.json
```

A task that is safe and below the configured spending threshold can be scheduled immediately. A task above the threshold enters `input-required` / `awaiting-owner` instead.

## Above-threshold approval

The approval signs the task ID, exact intent hash, policy version, decision, approval ID, and expiry. Changing the recipient, arguments, amount, or policy version after the proposal invalidates the approval.

Create an approval:

```sh
python3 scripts/owner-command.py \
  --owner-dir /path/to/owner-profile \
  approve \
  --task-id TASK_ID \
  --intent-hash INTENT_SHA256 \
  --policy-version 1
```

The live acceptance record in `evidence/above-threshold-live.json` shows a 6-unit request under a 5-unit per-transaction limit. It stayed in `awaiting-owner`, the owner notification was delivered, and no wallet effect was created.

## Encrypted owner channel

Deployment creates an owner Messaging identity and pins it in the agent profile. Owner requests and agent responses travel through Logos Messaging as encrypted, signed envelopes. There is no HTTP application server between the owner and agent.

`evidence/owner-channel-live.json` records a live owner-channel response from a separate Logos Core instance.

## Planner inference

Inference is optional and disabled by default. Connecting a planner does not grant it extra authority: model-selected tool calls still pass through the same task schemas, signed grants, spending policy, and effect reconciliation as CLI or Basecamp requests.

The optional Pi adapter lives under `adapters/pi/`. API credentials are read from
protected files by the model worker, not inherited from ambient environment
variables. They are not forwarded to third-party skill processes or returned in
status results.

Use **Inference settings** to select an API base URL, model ID, Responses or Chat
Completions format, output limit and price estimates. Saving settings makes no
model request. Remote endpoints require HTTPS; HTTP is accepted only for exact
local loopback hosts on the agent machine. A changed endpoint cannot inherit the
old endpoint's key. A replacement key is sealed to the selected agent before it
enters Messaging, then stored in an owner-only file.

Every model turn binds a signed configuration hash to an immutable snapshot,
including the endpoint, model, token limits, prices and credential digest. Changes
to the active snapshot or credential fail rather than selecting a fallback.
Prices are estimates used for the local budget, not provider billing guarantees.


## Verified conversation paths

The recorded Basecamp tests cover a read-only model request for stored files and
an action-enabled, zero-token-budget request for the dynamically installed
`example.text_statistics` extension. Both link to completed signed task records.
The model's words are not treated as the authority for completion. A failed
provider request remains in history and is not automatically repeated.

Switching agents keeps a separate unsent draft for each profile during this app
session and resets action permission. Drafts are not persisted across an app
restart. After a turn finishes, the next message defaults to read-only. Use
**Load earlier messages**, **Show full reply** and **Latest message** to read the
history. Every linked result opens the corresponding recorded task.
The owner may set a private `display_name` in a profile's `agent.json`; that is a
label only and never changes the signed agent identity or its key binding.


### Waiting for short peer tasks

The conversation runner observes the same zero-spend task for a bounded period
before returning its result to the model. Observation only reads the recorded
task; it never submits, executes or approves it again. A task that needs owner
approval stops immediately. Nonzero-spend tasks are not placed in this polling
window, and unknown payment outcomes still require reconciliation.

Capability counts in model-visible results are derived directly from completed
receipt arrays. The original receipts remain intact in Activity. A reply can
still be mistaken; the recorded task result, not the prose, is authoritative.


## Reading a finished task

Open Activity and View result. Completed tasks lead with a short summary derived
from their recorded result: file byte count and content reference, wallet balance
and observation block, or paid amount, provider and transaction reference.
**Show technical details** reveals the full bounded result or an explicitly marked
preview. A public program account's balance is not labelled as your wallet balance.

Pending approvals still show their full arguments before any signature. Collapsing
completed-task JSON does not hide what an approval authorizes. A missing or truncated
result is not treated as a verified success summary.


## Restart and action outcomes

An accepted action keeps its original task reference across restarts. The
conversation can reconstruct missing links from the engine's exact accepted
intent. Reading history only repairs metadata; it does not execute a task.
Explicitly starting an agent can restore the schedule for a previously accepted,
still-valid task. Expired actions are linked to their terminal result rather
than authorized again. Changes to price or policy after acceptance do not turn
that existing task into a new one.

A task that is still proving, working or checking an uncertain network result
remains pending. Once its recorded task finishes, the chat status can update
without another model request. Use the linked result for the returned data,
payment reference or failure. Do not send a duplicate solely because a proof is
slow or the app was closed.
