# Skill interface and extension SDK

Commons Relay exposes the 21 LP-0008 default skills plus `agent.ping`, a zero-cost liveness check for known peers. Their schemas are returned by
`meta.skills()` and are the same schemas shown to an optional planner. The task
engine validates those schemas before reserving a budget or calling an adapter.
A model response cannot create a new skill, alter a schema, or select a different
spending ceiling.

## Built-in interface

| Skill | Arguments | Spend policy |
| --- | --- | --- |
| `storage.upload` | `path`, `label` | zero |
| `storage.download` | `address`, `path` | zero |
| `storage.list` | none | zero |
| `storage.share` | `address`, `recipient` | zero |
| `messaging.send` | `recipient`, `message` | zero |
| `messaging.join` | `group_id` | zero |
| `messaging.create_group` | `members` | zero |
| `wallet.balance` | none | zero |
| `wallet.send` | `recipient`, `amount`, optional `payment_mode` | exact amount; private defaults to owner threshold, explicit public always requires owner review |
| `wallet.public_account` | none | zero; public receive address, initialization state and public balance |
| `wallet.initialize_public` | none | zero transfer; explicit owner approval to enable the public receiving account |
| `wallet.history` | none | zero |
| `program.query` | `program_id`, `params` | zero |
| `program.call` | `program_id`, `instruction`, `params` | zero LEZ transfer ceiling; always owner approval |
| `program.deploy` | `binary_path` | zero LEZ transfer ceiling; always owner approval |
| `agent.card` | none | zero |
| `agent.discover` | `topic` | zero |
| `agent.ping` | `agent_address` | zero; authenticated liveness check for a known peer |
| `agent.task` | `agent_address`, `skill`, `params`, optional `payment_mode` | exact advertised price; public requires explicit review and advertised support |
| `agent.subscribe` | `agent_address`, `task_id` | zero |
| `agent.cancel` | `agent_address`, `task_id` | zero |
| `meta.skills` | none | zero |
| `meta.status` | none | zero |
| `meta.configure` | `key`, `value` | zero; owner signature required |

`program.call` accepts a 64-hex LEZ program ID, a hex string containing the
little-endian u32 instruction words expected by that program, and
`params.accounts`, an ordered list of `{account_id, signer}` objects. The special
account ID `self` resolves inside the Wallet Core companion to the agent's own
public signing account. This generic escape hatch is deliberately not autonomous:
an arbitrary program can have effects the core cannot safely price from bytecode.

`program.deploy` reads a regular, non-symlink file below the agent's configured
`inputs` directory. The native wallet bridge independently checks the canonical
path and size before allowing the LEZ wallet companion to create a deployment
transaction.

## Adding a third-party skill without changing the core module

Third-party skills use an intentionally small subprocess SDK. They are not Python
plugins imported into the Relay worker. An owner installs an executable in a
trusted extension directory, pins its SHA-256 in a manifest, and lists the
manifest in the agent profile. Restarting the agent extends the immutable skill
registry from those manifests.

The extension execution SDK is **zero-spend**: an extension cannot debit the
provider's wallet. This does **not** prohibit a paid remote service. The provider
sets a service price in its signed listing; the A2A payment engine collects and
verifies that fee separately before running the extension. A public-eligible
extension can be installed and offered without editing the core module. See
`PROVIDERS.md` for `scripts/install-skill.py`, one-command deployment options and
the Basecamp listing editor. Extensions that themselves spend wallet funds need
a separately designed typed core adapter with explicit receipt
semantics rather than asking a generic subprocess to report its own cost.

Example `extensions.json` in the agent profile:

```json
{
  "manifests": ["text-count.json"]
}
```

Example manifest under `$COMMONS_RELAY_EXTENSION_ROOT`:

```json
{
  "schema": 1,
  "id": "example.text_count",
  "description": "Count characters in text",
  "executable": "text-count",
  "executable_sha256": "<64 lowercase hex characters>",
  "timeout_seconds": 10,
  "input_schema": {
    "type": "object",
    "properties": {
      "text": {"type": "string", "maxLength": 1000}
    },
    "required": ["text"],
    "additionalProperties": false
  },
  "public": false
}
```

The manifest name and executable are simple filenames, not arbitrary paths. The
loader rejects symlinks, hash changes, duplicate skills, reserved namespaces and
schemas with optional or additional fields. A manifest cannot replace a built-in
`wallet.*`, `program.*`, `agent.*`, `storage.*`, `messaging.*` or `meta.*` skill.

### Subprocess protocol

The executable receives one bounded JSON object on stdin and writes one bounded
JSON object on stdout. It gets an explicit environment containing only `PATH`,
`HOME`, `TMPDIR` and `LANG`, plus only credential names explicitly declared by
that manifest and provisioned as owner-only files by the installer. Relay does
not copy ambient API keys or the parent process environment. The operator must
trust installed executables; subprocess separation is not an OS sandbox. Stderr is never interpreted as a result.

Prepare has no side effect:

```json
{
  "protocol": "commons-relay-extension/v1",
  "phase": "prepare",
  "task_id": "...",
  "arguments": {"text": "hello"}
}
```

It returns a stable effect ID:

```json
{"effect_id": "my-effect-123"}
```

Relay durably records that ID before entering the broadcasting phase. Execute
then receives the same task, arguments and effect ID. It returns one of:

```json
{"state": "confirmed", "result": {"count": 5}}
{"state": "pending", "result": null}
{"state": "rejected", "result": {"reason": "unsupported"}}
```

If execution becomes ambiguous, Relay calls the same executable with
`"phase":"lookup"` and the previously recorded effect ID. The extension must
reconcile that effect instead of creating a new one. Crashes and timeouts fail or
leave only that task pending; they do not terminate the Relay Core worker.

The implementation is in `commons_relay/external_skills.py`, with executable
round-trip, hash, symlink, namespace and environment tests in
`tests/test_external_skills.py`.

## Native Core configuration and the included example

`COMMONS_RELAY_EXTENSION_ROOT` must be set in the environment that starts the
Logos Core daemon. The native Relay module validates and forwards that one path
to its isolated worker; no ambient model credentials are forwarded. Set
`extensions.json` in the chosen agent profile, then restart that idle agent.
The example in `examples/extensions/text-statistics.json` pins the accompanying
executable. Copy both files into the configured root and list that manifest in
`extensions.json`. It computes real word, character, line and UTF-8 byte counts
without a network call or token spend.

The next authenticated capability refresh shows the new tool in the existing
Basecamp interface. Neither QML nor the planner has a hardcoded menu entry for
that tool. The model can select it when the owner grants actions for a message;
read-only grants do not silently gain newly installed third-party permissions.

Executable integrity is checked again at each protocol phase. Output and stderr
are bounded while the process runs, and timeout/error termination applies only
to that extension task. These safeguards are not a claim that arbitrary hostile
executables are safe: extensions are trusted, operator-installed programs running
as the same operating-system user. Use additional OS isolation for untrusted code.

## Payment choice and cancellation

`payment_mode` is either `private` or `public`; leaving it out preserves the
private behavior of existing clients. The choice is part of the immutable task
arguments. The signed quote, transaction and refund use that same choice. A
provider that does not advertise public payment cannot be called in public mode,
and no failure or delay silently changes the privacy choice.

`agent.cancel` addresses an already accepted remote task using its task ID and
provider identity. It does not manufacture a new paid task. An unpaid task can
be canceled without payment. A payment already accepted on-chain cannot be
revoked; the provider follows the documented refund policy and the client waits
for a verified refund transaction. Completion or cancellation is reported from
the recorded peer state and receipt, not from a model's assertion. See
`a2a-payment-v1.md` and `PUBLIC-PAYMENTS.md` for quote and refund binding.

Public `wallet.send` accepts a lowercase 64-hex public account ID, or a name in
the owner's public payment address book. Private sending uses the separately
verified private recipient descriptor; the formats are deliberately not
interchangeable. Public and private balances are distinct. The optional public
initialization tool does not fund the account or convert private assets.
