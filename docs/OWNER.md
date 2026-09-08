# Owner interaction

Commons Relay separates the owner authorization key from the agent wallet and agent Messaging identity. The agent stores the owner's public authorization key. The owner directory holds the corresponding private key and should remain private.

## Basecamp

The owner UI is a loadable Logos app module under `native/ui/`. It uses `Logos.Theme` and `Logos.Controls`, so it follows the Basecamp theme rather than carrying a separate web design system.

The owner UI can:

- read agent status and current policy;
- inspect registered skills and task state;
- submit signed owner commands;
- approve an exact above-threshold intent;
- read owner-channel responses;
- show wallet, Storage, Messaging, and A2A status returned by the Core module.

The UI does not receive wallet private keys or an unrestricted shell. It calls the `commons_relay_module` methods exposed by Logos Core.

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
