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
- A peer service price comes from that pinned contact's verified Agent Card. The
  engine reserves that exact price before the A2A adapter starts. A changed quote
  fails rather than enlarging the reservation.
- Paid A2A services use a fresh private receiving account. The provider checks the
  actual private LEZ transaction, exact receiving commitment and exact amount;
  an incoming message that merely says paid is not a receipt.
- Files use libsodium XChaCha20-Poly1305 secretstream. File keys are sealed to a
  configured recipient's X25519 key and the outer Delivery message is signed.
- Core bridge operations are an allowlist. File paths and deployment binaries are
  canonicalized under configured roots. Storage's local deterministic peer
  helper accepts loopback addresses only.
- Third-party skill extensions are owner-installed, SHA-256 pinned subprocesses.
  They receive an explicit environment with no ambient API keys and are currently
  restricted to zero-spend skills.
- Owner and agent directories are separate. The agent stores the owner public key,
  not the private authorization key. Wallet spending/nullifier secrets remain in
  the Wallet Core profile; the Messaging identity receives domain-separated child
  seeds, not the wallet root secret.

Known limitations:

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
