# LEZ testnet compute and proof cost

This document records the compute observations used by Commons Relay at the pinned
LEZ revision `47eba256479f6f785acbd138834340703cd03401`. These numbers are evidence for
the current testnet implementation, not a promise about later fee schedules.

## What the current LEZ API exposes

At this pinned revision, the public program executor has a hard session ceiling of
`32 * 1024 * 1024` RISC0 cycles. The source immediately above that constant says
`TODO: Make this variable when fees are implemented.` The wallet source also has a
`GasConfig` type, but the gas fields are not referenced elsewhere in this pinned
LEZ checkout. The sequencer/indexer transaction lookup returns the transaction,
not a receipt containing an independently metered compute-unit or gas-used value.

For that reason Commons Relay does **not** invent a token-denominated CU charge.
Where LEZ currently exposes no finalized metered CU receipt, the table below uses
the closest reproducible quantities available today:

- public program execution: actual RISC0 guest user cycles measured with the same
  program image and ABI;
- private token transfer: real `RISC0_DEV_MODE=0` proof-generation wall time plus
  the confirmed transaction/block receipt;
- program deployment: compiled program byte length and confirmed transaction/block
  receipt. Deployment is a state transition that stores the supplied program
  binary; the current pinned sequencer does not return a gas-used/CU-used field.

When LEZ adds a metered compute receipt, this document and the demo report should
record that receipt directly instead of deriving a number from these proxies.

## Public program call measurements

`evidence/commons-guest-cycle-bench.json` was produced by actual RISC0 guest
execution on synthetic benchmark inputs. It excludes the outer privacy circuit,
does not generate a proof, and is deliberately marked `not_a_gas_price`. The unit
is RISC0 user cycles excluding continuation/po2 padding.

| Operation | Input size | Guest user cycles |
| --- | ---: | ---: |
| allowlist.create | 3 / 10 / 256 members | 59,923 |
| allowlist.claim | 3 members | 554,527 |
| allowlist.claim | 10 members | 592,883 |
| allowlist.claim | 256 members | 669,595 |
| threshold.create | 3 / 10 / 256 members | 63,340 |
| threshold.propose | 3 members | 546,826 |
| threshold.propose | 10 members | 585,182 |
| threshold.propose | 256 members | 661,894 |
| threshold.approve | 3 members | 570,893–584,572 |
| threshold.approve | 10 members | 609,249–622,928 |
| threshold.approve | 256 members | 685,961–699,640 |
| threshold.execute | 3 / 10 / 256 members | 116,216 |

These values are far below the pinned 32 Mi-cycle public-execution ceiling, but
that ceiling is a limit rather than a fee schedule.

## Token transfers

Commons Relay's paid A2A path uses a private authenticated-transfer transaction.
The wallet always sets `RISC0_DEV_MODE=0` and uses the IPC prover. The initial
three shield operations were real testnet private proofs:

| Agent | Real proof wall time | Confirmed block |
| --- | ---: | ---: |
| Storage | 2,447.099 s | 42,000 |
| Messaging | 2,461.303 s | 42,000 |
| Blockchain | 2,507.593 s | 42,001 |

Those measurements used two Rayon proof threads on an M1 Pro. Wall time is a
machine-dependent engineering measurement, not an LEZ network CU price. An early
paid A2A preparation generated another real private proof in 2,498.912 s; that
transaction was intentionally not broadcast after the enclosing IPC result failed
deterministic serialization.

After fixing that serialization boundary, a fresh paid A2A task completed on the
public testnet using six Rayon proof threads:

| Operation | Real proof wall time | Confirmed block | Result |
| --- | ---: | ---: | --- |
| A2A private 3-unit payment | 964.433 s | 42,557 | client 50 → 47, provider 50 → 53 |

The complete sanitized receipt is `evidence/paid-a2a-private-lez.json`. Thread count
and machine load differed between these runs, so they are not a controlled
speedup experiment. The difference is not evidence of a protocol fee change.

The wallet records the exact transaction hash before network submission and later
requires that exact hash and exact receiving commitment/amount when confirming an
A2A payment. This prevents the performance instrumentation from becoming the
source of truth for spending.

## Program deployments

`program.deploy` validates a compiled LEZ program binary, prepares a
`ProgramDeploymentTransaction`, records the exact program ID and transaction hash,
and only then broadcasts. For each demo deployment the acceptance report should
record:

1. program binary byte length;
2. derived program/image ID;
3. transaction hash;
4. confirmed block;
5. proof wall time if the upstream transaction type requires proving.

The pinned LEZ code does not expose a metered deployment-CU field. The otherwise
present `gas_fee_per_byte_deploy`/`gas_cost_deploy` configuration fields are not
consumed by this pinned checkout, so multiplying binary length by those fields
would fabricate a fee that the running protocol does not currently charge or
report.

## Reproducing the measurements

The full benchmark JSON is retained under `evidence/` in the development
workspace. The submission demo should emit a compact public report containing
only aggregate cycles, program IDs, byte counts, proof duration, transaction
hashes and block IDs. Private witness data, wallet keys and RISC0 journals are
never written to the public report.

For all private proof demonstrations verify both of these in the terminal before
and during the operation:

```text
RISC0_DEV_MODE=0
RISC0_PROVER=ipc
```

The Core wallet companion independently forces the same values, so changing the
caller environment cannot silently downgrade a prize demo to dev-mode proving.

## Clean local proof verification, 8 September 2026

The corrected local demo first claimed the fresh payer's genesis deposit through
the pinned vault program, then prepared and confirmed a private 5-unit shield
with `RISC0_DEV_MODE=0` and the local IPC prover. Independent wallet inspection
returned a private balance of 5. Proof generation took 2,046,077 ms and the
measured prepare/confirm/check flow took 2,064,244 ms on macOS arm64. These are
wall-clock measurements, not on-chain compute units or fees.

See `evidence/local-real-proof-macos.json`. This proves a clean local flow; it
does not replace the public-testnet evidence or the submission video.

## Current-network observations, 11 September 2026

Older blocks in the tables above are historical observations. They are not current
wallet balances after a testnet reset.

The public custom text-statistics service completed for **1 testnet unit** at
block **3206**. Its recorded private proof wall time was **2678.719 seconds**;
the entire task took **2844 seconds**. Both figures come from
`evidence/paid-public-extension-20260911.json`. The executable's result is separate
from proof of the token transfer: the payment proof does not prove the word count.

The chat-initiated Windows Exa service also completed for **1 testnet unit**, at
block **3359**, transaction
`0fab8d18f35aa2771c692dc43dcc9f67deb7aa01ff8f90bc3a563a1720f27ec0`.
Its engine-recorded complete workflow took **2670 seconds**. That duration includes
more than proving and must not be labeled as a standalone proof benchmark. The
provider's result hash matched the client's recorded artifact. The three-result
request returned a shortened response containing two saved linked excerpts; this
is a service-output limitation, not an additional token charge. See
`evidence/chat-windows-exa-verified-20260911.json`.

The new public program-call example compiles to **311876 bytes** in the current
Mac build. Build completion is not deployment or call completion. Its UI
acceptance must record its own program ID, transaction and block before being
counted as a successful operation.

`program.deploy` and the currently supported `program.call` use public
transactions at the pinned revision. They are always owner-approved, but do not
require the long private transfer proof. Generic program effects are not inferred
from a zero direct-token quote; review the program and account list.

No NVIDIA acceleration is claimed. CUDA driver detection and compiling part of a
prover are not evidence of a GPU-generated, verified proof. The unfinished GTX
1060 experiment is excluded from the required demonstration.

## Default-skill UI observations, 11 September 2026

These are the engine's elapsed task durations, including approval, queuing,
network settlement and response handling. They are **not standalone proof times**
and are **not on-chain metered CU**.

| Default skill | Task elapsed | Confirmed block | Direct token spend |
| --- | ---: | ---: | ---: |
| wallet.send | 2,766 s | 3703 | 1 testnet unit |
| program.deploy | 118 s | 3639 | 0 |
| program.call (corrected instruction) | 123 s | 3711 | 0 |

The matching task IDs and transaction hashes are in
`evidence/default-wallet-program-ui-20260911.json`; these were rechecked against
the current chain without broadcasting again. The public call used instruction
`4554494b`. The earlier `4544494b` transaction is a separate failed-validation
case, not part of the success measurement.

The same no-change program was separately executed with a saved public account
snapshot using the pinned guest executor: 41,855 guest user cycles. That local
execution is not a zero-knowledge proof or the sequencer's metered fee receipt.
The deployed image ID is
`f863b88a398597e3f869d6ea831738c74ad36063c87fce84d6a9e4bf0c8f4829`.
