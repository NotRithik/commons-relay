# Security and authority model

Commons Relay is testnet software. The boundaries below are design constraints,
not a claim that the surrounding operating system or third-party Logos modules
are trusted execution environments.

- The planner proposes typed calls. It does not hold wallet keys or owner signing
  keys and cannot alter the registry or policy.
- The task engine verifies signatures, schemas, expiries, policy version and
  spending reservations before any adapter effect.
- Prepared transaction/effect references are persisted before broadcast.
  Ambiguous network outcomes reconcile the recorded effect; they do not create a
  replacement payment.
- A service price comes from a verified Agent Card: either an owner-pinned peer
  or a self-authenticating public service identity. The engine reserves the exact
  price before the A2A adapter starts. The quote binds the client, provider, skill,
  input hash, task, receiver, amount and expiry. A changed quote fails rather than
  enlarging the reservation.
- Public discovery is separate from owner trust. A verified public key enters the
  service-contact table, never the owner's contact table. Its encrypted channel
  admits A2A traffic, not owner commands, private file sharing or group control.
  A signature establishes key possession, not reputation or answer correctness.
- Discovery and incoming messages have bounded parsing, signature-check rates,
  schemas, expiry checks and per-peer task isolation. These limits do not solve
  Sybil attacks or provide guaranteed delivery in a disconnected network.
- Paid A2A services use a fresh private receiving account. The provider checks the
  actual private LEZ transaction, exact receiving commitment and exact amount;
  an incoming message that merely says paid is not a receipt.
- Files use libsodium XChaCha20-Poly1305 secretstream. File keys are sealed to a
  configured recipient's X25519 key and the outer Delivery message is signed.
- Core bridge operations are an allowlist. File paths and deployment binaries are
  canonicalized under configured roots. Storage's local deterministic peer
  helper accepts loopback addresses only.
- Third-party skill extensions are owner-installed, SHA-256 pinned subprocesses.
  They receive an explicit environment, plus only credentials explicitly installed
  for that exact skill. Ambient API keys and wallet paths are not inherited.
  Extensions cannot debit the provider wallet through this interface. They may
  be offered as paid services: the core verifies the client's payment before
  running the extension. Receiving a service fee is not spending authority.
- Owner and agent directories are separate. The agent stores the owner public key,
  not the private authorization key. Wallet spending/nullifier secrets remain in
  the Wallet Core profile; the Messaging identity receives domain-separated child
  seeds, not the wallet root secret.

## Durable messaging and recovery

Transport acknowledgement means a message was durably received, not that its
requested task succeeded. Processed control messages and acknowledgements move
out of the bounded active inbox into an archive. Original message IDs, signed
bodies and monotonically increasing sequence numbers remain available for replay
checks and history reads. Restarting does not clear uncertain task/payment state.
The archive is retained on disk; operators need adequate storage and backups.
A full active inbox is a delivery failure, never an implicit approval.

## External services and optional remote proving

An Exa-backed service sends its query to Exa. The encrypted Logos hop does not
hide that query from the selected provider or the upstream API. Provider API
credentials stay out of Agent Cards, task arguments and results. Upstream API
charges and availability are separate from the client's LEZ spending cap.

The optional SSH prover transports confidential proving inputs, not merely public
transaction data. The worker must therefore be trusted even when wallet files
stay on the client. Host-key checks, a pinned prover executable and local receipt
verification are necessary; none makes an untrusted prover confidential. A
compiled GPU binary is not evidence of a verified proof or measured speedup.
Do not select a remote prover until its real receipt validation has passed.

## Known limitations

- A compromised OS user, malicious code with permission to read both owner and
  agent directories, or a compromised Logos dependency is outside these software
  boundaries.
- The extension subprocess boundary limits crash/credential propagation but is not
  a kernel sandbox in production. Install only reviewed extension executables.
- Storage DHT provider propagation is asynchronous. Acceptance tests can add an
  explicit loopback peer for deterministic same-machine retrieval; that does not
  replace public-network replication testing.
- Generic `program.call` cannot infer arbitrary program economic effects, so it is
  owner-approved even when its direct LEZ transfer ceiling is zero.
- This repository is testnet-only until its transaction/accounting assumptions and
  upstream interfaces are independently reviewed for a production release.
