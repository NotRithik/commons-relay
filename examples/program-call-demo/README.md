# Small public program for the deploy/call walkthrough

This example exercises the real `program.deploy` and `program.call` skills. It is not a replacement for the required private-wallet proof demonstration.

The program accepts one `u32` instruction tag (`0x4b495445`) and between one and sixteen accounts. It returns the supplied account balances, data and ownership unchanged. It has no token transfer or mint operation. The normal LEZ transaction nonce and receipt behavior still applies.

## Build

Use the repository's pinned LEZ revision and RISC Zero compiler. The lockfile is included. From the repository root:

```sh
CARGO_BUILD_JOBS=1 python3 scripts/build-rust.py guest \
  --manifest examples/program-call-demo/Cargo.toml \
  --target-dir out/program-call-demo \
  --guest-rustc "$HOME/.rustup/toolchains/risc0/bin/rustc"
```

This only compiles. It does not contact a sequencer or create a transaction. The raw executable is `out/program-call-demo/riscv32im-risc0-zkvm-elf/release/kite-public-call-demo`.

**A raw ELF is not a deployable program at this LEZ revision.** Pack it with the pinned compatibility kernel using the current wallet companion, then inspect it without loading any wallet:

```sh
/path/to/commons-relay-wallet pack-program \
  out/program-call-demo/riscv32im-risc0-zkvm-elf/release/kite-public-call-demo \
  out/program-call-demo/kite-public-call-demo-packed.bin
/path/to/commons-relay-wallet inspect-program \
  out/program-call-demo/kite-public-call-demo-packed.bin
```

Both commands are offline and submit no transaction. Packing refuses an existing destination. The output includes the packed byte count, SHA-256 and program ID. Use the packed `.bin`, not the raw executable, for deployment.

## Use in Basecamp

Copy the packed program into the chosen agent's configured `inputs` directory as `kite-public-call-demo-packed.bin`. In Kite, choose the agent, then Skills & tools and `program.deploy`. The file argument is `kite-public-call-demo-packed.bin`, without an `inputs/` prefix.

Review and send the task. Program deployment is always held for an explicit owner approval, even with a zero token ceiling. Approve the exact file only after reviewing its source and hash. Keep the task ID; do not send another deployment while its outcome is uncertain.

After the deployment is confirmed, use its returned program ID with `program.call`. Use instruction `4554494b` (the little-endian encoding of the tag), and these parameters:

```json
{"accounts":[{"account_id":"self","signer":true}]}
```

`self` is resolved by the wallet companion to this agent's existing public signer account. It is not a private key and it does not grant a foreign program access to the owner authorization key.

The call is also held for owner review. Check the program ID and account list before approving it. Confirm the resulting transaction on the current testnet and compare the account data and balance before and after. Do not infer success from the presence of a task or an approval.

At the pinned implementation these two operations use public transactions. They do not require the long private payment proof. Generic programs can have economic effects; this example's no-change behavior must not be assumed for other bytecode.

## Evidence boundaries

A successful build alone proves neither deployment nor execution. Record the source/binary hashes, task IDs, transaction hashes and confirmation blocks only after the actual UI flow finishes. This directory intentionally contains no wallet, owner keys, or live profile.
