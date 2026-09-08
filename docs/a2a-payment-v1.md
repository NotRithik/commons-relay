# Relay private LEZ payment extension, version 1

This is an explicitly advertised A2A extension for the pinned Logos testnet. It
is not AP2, x402, or a claim of compatibility with an unrelated payment scheme.
The extension is carried over the Logos Messaging binding documented alongside
this file. Amounts are decimal strings of integer LEZ testnet base units.

## Quote and payment

The provider returns an A2A task in `TASK_STATE_INPUT_REQUIRED`. The task metadata
contains an immutable quote bound to the provider, client, task ID, context ID,
skill, arguments digest, exact amount, asset, network, expiry and refund-address
digest. Each paid task receives a fresh private receiving account descriptor.
The payer checks every binding and refuses a price above the owner's reserved
maximum. The model cannot enlarge that maximum or choose a different recipient.

The payer prepares a real private LEZ transaction and persists its complete hash
before broadcast. It then continues the same A2A task with the quote ID and
transaction hash. The provider does not trust that hash by itself: its wallet
fetches the transaction, synchronizes its private account, checks the exact amount
and account owner, and verifies that the recipient account commitment appears in
that transaction's outputs. A transaction cannot pay two recorded tasks.

## Fulfillment and refund

Only a verified payment unlocks a paid service. Completion returns a standard
A2A artifact. Failure or accepted cancellation before successful fulfillment
queues a full refund to the fresh refund account supplied in the signed original
request. Refund preparation, hash persistence, broadcast and reconciliation use
the same wallet pipeline as ordinary payments. A late payment to an already
canceled task is refunded rather than silently retained.

The client releases a failed task's reserved budget only after its own wallet
verifies the refund transaction and receiving commitment. A timeout is not a
refund receipt. The provider never treats a lost broadcast response as permission
to send another payment.

The paid path has also been exercised on the public LEZ testnet. The sanitized
receipt in `evidence/paid-a2a-private-lez.json` records a 3-unit private payment,
confirmed block 42557, client balance 50 → 47, provider balance 50 → 53, and the
completed provider `program.query` result. The proof ran with `RISC0_DEV_MODE=0`.

Refund and failure handling remain covered by deterministic acceptance tests; a
test fixture is never treated as a network payment receipt.
