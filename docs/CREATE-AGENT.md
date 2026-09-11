# Create an agent in Basecamp

## What the button creates

**Create an agent** starts a separate local Logos Core instance, Kite's persistent
Python task runtime, and the native Storage, Messaging and wallet companion
modules. The agent gets its own wallet and Messaging identity. It does not inherit
another agent's funds, API key, tasks or public service listing.

Pi is the optional Node-based planning loop, not the persistent task engine. When
an installed Pi runtime is available, the owner can configure it using **Model
settings**. Inference remains disabled until the owner saves the model settings.
Saving settings is not an inference request. Requests still need their own signed
scope and remain subject to the configured shared budget and wallet policy.

The three category agents used in the prize demonstration are examples. Every
agent uses the same extensible skill registry; its name is not a fixed role.

## Owner flow

1. Open Kite in Basecamp and choose **Create an agent**.
2. Give it a name, choose **Review setup**, and inspect the location and defaults.
3. Choose **Create this agent** once. The progress view tracks that exact setup.
4. When ready, choose **Connect to this agent**. Wait for a fresh encrypted reply.
5. Configure a model separately, or use **Skills & tools** without any model.
6. To offer a public service, open **Services**, choose the services and prices,
   review the public information, and explicitly publish the listing.

**Agents you control** lists saved owner connections, not every public agent.
**Find a service** searches public listings. Discovering a provider gives neither
party authority over the other's owner settings, files or wallet.

The dialog creates an agent on this computer. Remote hosts use the headless
command in DEPLOYMENT.md; this is not a remote provisioning wizard.

## Recover, do not duplicate

Closing the window does not launch another setup. Reopening restores the latest
active or failed setup. Repeated review for the same name reuses its existing job.
A failure preserves the profile and wallet. Choose **Review recovery**, check that
it names the existing agent, then resume it. A new review binds the current runtime
files; an older review cannot silently authorize changed deployment code.

The installer uses a short, private socket directory derived from the Core
session path. This avoids the Unix-domain socket length limit in deep project
folders. The directory is owned by the current user with mode 0700. No live socket
or another user's directory is removed to obtain a name. A failed first Core
startup is tracked before readiness polling and stopped before agent configuration.

A sandboxed distribution must permit file and local socket access to its dedicated
private IPC root, `/private/tmp/kite-<uid>` on macOS or `/tmp/kite-<uid>` on Linux.
Do not disable the sandbox or grant all of `/tmp` merely to enable this feature.

## Distribution integration

The installed UI needs its normal private owner-profile root and a private
`.setup-runtime.json` in that root. This is installer configuration, not a model
or remotely supplied command. The UI accepts a name and reviewed job identifier;
it never accepts shell text or arbitrary executable paths.

The configuration contains these fields:

| Field | Meaning |
| --- | --- |
| `schema` | Integer 1 |
| `deploy_script` | Absolute path to the packaged `scripts/deploy-agent.py` |
| `logosctl`, `modules_dir` | Verified headless Core executable and installed modules |
| `wallet_binary`, `sodium_library` | Wallet companion executable and crypto library |
| `storage_preset` | Explicit Logos Storage network preset |
| `state_root`, `wallet_root`, `session_root` | Existing private parent directories for new profiles |
| `owner_transport_root` | Existing local owner Messaging profile |
| `owner_contact` | Public contact exported by that owner transport; never private keys |
| `bootstrap_nodes`, `cluster_id` | Existing loopback Messaging peers and cluster |
| `risc0_server_path`, `lbc_root_dir` | Pinned proof executable and circuit assets |
| `planner_runtime` (optional) | Installed Pi `node`, `runner`, `runner_sha256`, shared `budget_file` and `budget_micro_usd` |

The optional planner configuration references an existing shared budget; it does
not reset or enlarge it. No API key field is accepted in the setup configuration.
The current local dialog supports existing loopback Messaging peers. Internet/LAN
bootstrap configuration for remote deployment is covered in NETWORKING.md.

Validate and preview an installer-prepared configuration:

```sh
python3 scripts/configure-owner-setup.py \
  --owner-root "$OWNER_PROFILES" --config "$PRIVATE_SETUP_CONFIG"
```

Install exactly that reviewed configuration using the printed hash:

```sh
python3 scripts/configure-owner-setup.py \
  --owner-root "$OWNER_PROFILES" --config "$PRIVATE_SETUP_CONFIG" \
  --apply "$REVIEW_HASH"
```

Both commands are configuration operations only: neither creates an agent,
requests funds, nor invokes a model. The configuration, owner root and budget file
must be private. The installer helper refuses changed inputs or an altered prior
configuration rather than overwriting another installation silently.

## Acceptance scope

The initial Research assistant creation exposed a long socket path and a delayed
Core-startup issue. Its same-identity UI recovery, owner connection and first
zero-cost task were verified. These failures are not hidden as a clean first run.
Fresh setup and model-configuration acceptance are tracked separately in
IMPLEMENTATION-PLAN.md until their actual UI checks finish.
