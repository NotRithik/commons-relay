# Choosing private or public payments

The live public Exa request completed through Basecamp chat with a one-unit
payment at block 3914. A separate deliberately invalid JSON-formatting request
failed, and its full public refund was independently verified by the client.
See `evidence/public-exa-chat-20260911.json` and
`evidence/public-failed-service-refund-20260911.json`. These observations do not
replace final-version cancellation, concurrency and release acceptance.

## What the choice means

**Private** is the default and preserves the existing shielded transfer flow.
It requires a real proof, which can take tens of minutes on a laptop. The
payment remains pending until the exact transaction is confirmed.

**Public** spends the agent's public balance. Sender, recipient and amount are
visible on-chain. It does not generate a private-payment proof, but still waits
for network acceptance and confirmation. It is not a promise of instant finality.
Private funds are never automatically converted into public funds.

In Services, a paid request offers public mode only when the provider's signed
card advertises it. The review screen repeats the selected mode, exact price and
privacy consequence. Public mode requires an exact owner approval, even when the
amount is below the ordinary autonomous spending threshold. Existing authorized
private requests retain their existing behavior.

## Direct wallet actions

`wallet.send(recipient, amount, payment_mode?)` retains its original required
arguments. Omitting `payment_mode` means `private`; the optional values are
`private` and `public`.

A public recipient is a 64-character lowercase hexadecimal public account ID or
an operator-installed alias from `public-payment-addresses.json`. It is not the
private receiving descriptor. The recipient must already have public receiving
enabled. The Rust wallet independently checks initialization and available public
balance before preparing the transfer.

`wallet.public_account()` reads the public receiving account, its initialized
state, public balance and observed block. It does not initialize or fund anything.
`wallet.initialize_public()` is a separate, explicitly approved, zero-transfer
on-chain action. No faucet claim or balance conversion is hidden in either call.

## Provider setup

Public payments are off by default. A provider first enables its public receiving
account, then opts into **Also accept public payments** in Offer a service. The
runtime checks its receiving account before accepting that setting. Its signed
Agent Card then advertises `paymentModes: ["private", "public"]`; otherwise the
card advertises only private payments. Unknown or absent modes never authorize a
public transfer.

## Quote and receipt binding

A public task request adds `paymentMode: "public"`. Its quote repeats that choice
and binds the existing task ID, provider, client, skill, arguments hash, amount,
network, expiry and refund-address hash. The public recipient descriptor contains
its account ID and the observed block. A public quote cannot be substituted for a
private one, nor can a changed quote be accepted after payment preparation.

A public transaction hash alone is **not** a proof that the requesting agent paid:
any observer can copy a public hash. The payer wallet therefore creates a
separate domain-separated signature binding the exact quote hash, confirmed
transaction hash, network and purpose (`payment` or `refund`). The signer is the
actual public payer account. The wallet only creates this claim for its own
recorded, confirmed public-transfer operation and persists the claim for
idempotent announcements. That operation cannot later be attested against a
different quote.

The receiver checks the signature and account derivation, then independently
reads the transaction from LEZ. It must be a confirmed public authenticated
transfer with exactly the expected sender, receiver and amount, in a block after
the quote's observed block. The provider's durable uniqueness constraint prevents
one payment from paying multiple tasks. No service is fulfilled merely because
a model or client says it paid.

## Cancellation and refund

Cancellation retains the existing lifecycle policy: a paid task that has not
successfully fulfilled is refunded; successful work is not silently undone.
A public refund uses public funds and is itself publicly visible. Its attestation
is bound to the same quote with purpose `refund`. The client independently checks
the provider as sender, its own refund account, amount, confirmation and block,
and prevents the same refund hash from settling two tasks.

A timeout or lost reply does not trigger a new payment. Prepared transactions and
uncertain network outcomes retain their original operation IDs. Explicit mode
selection is never treated as permission to replay a task or change privacy after
an error.

## Compatibility and limits

Older private task requests and quotes keep their original wire form. New public
fields are added only to public requests. The documented Logos Messaging binding
remains the transport; public payments do not introduce an HTTP server or change
owner-channel encryption.

Public transfer visibility is not the same as publishing task content: the task
inputs and results still travel over the encrypted agent channel. The signature
claim authenticates payment ownership, not service quality. There is no escrow or
independent enforcement against a dishonest provider refusing to deliver/refund.

This feature does not enable simultaneous writers on the same wallet. Separate
agents can use separate wallets concurrently; a single wallet's note/nonce and
pending-operation guards remain in force.
