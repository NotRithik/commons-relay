# Skill interface and extension SDK

Commons Relay exposes 21 built-in skills. Their schemas are returned by
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
| `wallet.send` | `recipient`, `amount` | exact amount; owner threshold applies |
| `wallet.history` | none | zero |
| `program.query` | `program_id`, `params` | zero |
| `program.call` | `program_id`, `instruction`, `params` | zero LEZ transfer ceiling; always owner approval |
| `program.deploy` | `binary_path` | zero LEZ transfer ceiling; always owner approval |
| `agent.card` | none | zero |
| `agent.discover` | `topic` | zero |
| `agent.task` | `agent_address`, `skill`, `params` | exact price from the pinned peer's signed Agent Card |
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

The current extension SDK is **zero-spend only**. This is a safety boundary, not
a missing price field: an extension cannot debit the agent wallet. Financial
extensions should be implemented as typed core adapters with explicit receipt
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
`HOME`, `TMPDIR` and `LANG`; Relay does not copy API keys or the parent process
environment into it. Stderr is never interpreted as a result.

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
