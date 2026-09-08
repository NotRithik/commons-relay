# Owner interaction

Commons Relay separates the owner authorization key from the agent wallet and agent Messaging identity. The agent stores the owner's public authorization key. The owner directory holds the corresponding private key and should remain private.

## Basecamp

Relay is a control panel for agent instances. The Storage, Messaging and Blockchain
names identify three independently deployed demo agents required by the prize.
They use the same extensible registry; one instance can choose among all of its
permitted tools. They are not fixed-response bots or mandatory task categories.

Choose an instance, then wait for an authenticated owner-channel reply. Opening
the app proves neither an agent connection nor a completed task.

**Chat** accepts ordinary language. Read-only is the default. The owner must accept
the model data-sharing notice before sending a message. To allow file changes,
messages or a payment, enable actions and review the goal and maximum test-token
spend. The owner's local signer creates a bounded goal grant; the remote planner
gets only delegated authority. Per-task limits and exact approvals still apply.
A missing model configuration disables Send and explains why. Suggestions merely
fill the input; they do not execute a hidden fixed scenario.

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

The optional Pi adapter lives under `adapters/pi/`. API credentials belong in the planner process environment and are not forwarded to third-party skill subprocesses or returned through agent status APIs.


## Verified conversation paths

The recorded Basecamp tests cover a read-only model request for stored files and
an action-enabled, zero-token-budget request for the dynamically installed
`example.text_statistics` extension. Both link to completed signed task records.
The model's words are not treated as the authority for completion. A failed
provider request remains in history and is not automatically repeated.

Switching agents clears the draft, action permission and provider consent. After
a conversation finishes, the next message defaults to read-only again. Use
**Latest reply** to reach the newest response without losing earlier records.
The owner may set a private `display_name` in a profile's `agent.json`; that is a
label only and never changes the signed agent identity or its key binding.
