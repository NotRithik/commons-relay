# CommonsRelay architecture

CommonsRelay is a Logos Core application, not a set of fixed demo conversations. The
native owner interface runs beside Commons in the same Basecamp instance. The
Core plugin owns a persistent local Python worker. That worker stores task and
permission state in SQLite and delegates effects to adapters.

## The three boundaries

**Planner.** A planner turns an authorized goal and previous results into tool
proposals. Pi's `Agent` loop can supply this layer, as can another local or hosted
model. The planner is not the wallet. It does not hold the owner's signing key,
choose its own spending ceiling, or bypass validation by writing a reassuring
explanation. A model connection is optional and disabled in the installed build.

**Task and permission engine.** This is ordinary deterministic code. An owner
signs a goal grant naming the delegate key, permitted skills, maximum number of
steps, expiry and total budget. Each step is separately signed by the delegate
and bound to that grant. The engine validates the tool's arguments and trusted
cost quote, then reserves the amount atomically. Per-transaction and rolling
period limits still apply. A goal grant does not bypass a wallet limit.

**Effect adapters.** The adapter implements the operation using the actual Logos
Storage, Messaging or LEZ API. It never treats the planner's claim of success as
confirmation. Preparation, broadcast and reconciliation are separate operations.
The native bridge has a fixed operation table and constrained file roots; it is
not an arbitrary shell or an unrestricted module-call proxy.

Adding a useful capability means registering a typed skill, a trusted validator
and cost function, and an adapter. It does not mean adding another hardcoded
conversation or changing the planner loop.

## Why retain a small custom core rather than fork a whole harness?

Pi already provides the model/tool loop, conversation state and streaming events.
Those are reusable. A coding-agent harness normally also includes filesystem and
shell tools. Giving that entire tool surface the same authority as an autonomous
wallet would be the wrong boundary for this application.

CommonsRelay keeps financial authorization, exact request binding, task persistence,
message authentication and failure recovery outside the harness. Its Pi adapter
exposes only registered CommonsRelay tools. A tool result that requires owner input
ends that automatic step rather than asking the model to invent approval.

Pi reference: https://github.com/earendil-works/pi/tree/main/packages/agent
The project was previously hosted under `badlogic/pi-mono`.

## Requests and approvals

Amounts are decimal strings in integer testnet base units. Floating-point money
and ambiguous representations such as `1e3` or `01` are rejected. The signed
intent includes the agent, requester, skill, arguments, asset, spending bound and
policy version. Reusing a request ID with different content is rejected; replaying
the same signed request returns the existing task.

An above-threshold action enters `input-required`. A separate owner signature
must match its exact intent hash. The approval has its own replay-resistant ID
and expiry. Owner notification failures are retried with bounded backoff, never
converted into permission. Revoking a goal cancels its queued work and is checked
again before a prepared effect can be broadcast.

Signatures use Ed25519 through OpenSSL, not a home-made signature algorithm.
The owner and agent have separate profile directories. The agent receives the
owner's public key, not their private signing key. This is a software boundary;
a compromised operating system or a malicious module with access to both
profiles remains outside the protection offered by it.

## Task persistence and uncertainty

The engine commits `broadcasting` before calling the network adapter. If the
process dies or a response disappears, the task becomes `unknown` internally and
its reservation remains held. The next process reconciles the known effect
reference; it does not submit the same payment again as a new task.

Preparation failures can safely release a reservation because no broadcast has
occurred. A definitive rejection can also release it. An ambiguous timeout
cannot. Confirmed spending and outstanding reservations both count against the
rolling budget. Concurrent requests use a SQLite `BEGIN IMMEDIATE` transaction,
so two workers cannot each reserve the same remaining budget.

The A2A transport maps internal uncertainty to an in-progress task with explicit
reconciliation metadata. It must not invent a non-standard A2A terminal state or
report `completed` while the underlying network result is unknown.

## File confidentiality

The vault uses libsodium's XChaCha20-Poly1305 secretstream API. Files are encrypted
in 64 KiB chunks, with a required final authentication tag. Labels are inside the
encrypted metadata. Different uploads use fresh stream headers and keys.

A download is decrypted to a temporary owner-only file. Its final destination is
created only after the entire stream authenticates; a truncated or corrupt file
cannot leave a partially accepted document behind. File operations reject parent
traversal, symlink components and overwrites outside the configured roots.

Sharing seals the file key to the recipient's X25519 public key and sends it over
the authenticated messaging channel. A sealed box provides recipient
confidentiality, not sender identity by itself; sender authentication comes from
the signed transport envelope and configured peer identity.

The SQLite metadata and local key files are protected by file permissions, not
claimed to be encrypted against a compromised OS. No private keys or file keys
are returned as model tool results or shipped in release packages.

Library references:
- https://doc.libsodium.org/secret-key_cryptography/secretstream
- https://doc.libsodium.org/public-key_cryptography/sealed_boxes

## Logos and A2A

The native Core module talks to other installed modules through Logos IPC and
receives their actual asynchronous events. Owner requests and agent-to-agent
messages use Logos Messaging rather than a hosted intermediary. Each agent has
its own profile and shielded testnet account.

The transport binding targets the A2A 1.0 data model: Agent Cards declare the
custom interface, tasks retain stable identifiers, and status events can resume
from a cursor. Payment authorization is an explicit extension bound to a task,
quote, recipient, asset and amount. An unverified message saying paid is not a
receipt. The live adapters and interoperability tests are still being completed;
a registry entry alone is not evidence that a skill has executed on testnet.

Protocol reference: https://a2a-protocol.org/latest/specification/

## What is verified now

The native Core and owner UI load in Basecamp, and the UI reads the actual local
worker through IPC. Tests cover signed requests, goal limits, concurrency,
restart persistence, ambiguous sends, expiry, file encryption, corruption,
truncation and constrained filesystem operations. These tests are separate from
real network acceptance tests. The installed planner is off, and development
has not invoked a paid model or hosted prover.
