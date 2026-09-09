# LP-0008 criterion map

This file maps the September 2026 LP-0008 requirements to source, tests, and sanitized live evidence. It is meant to make independent review faster; it does not replace the prize specification.

## Functionality

| Criterion | Current evidence |
| --- | --- |
| Core module loads beside wallet, Storage, and Messaging | `evidence/module-load.json`; native modules under `native/` |
| Independent shielded LEZ wallet | `evidence/three-testnet-agents.json`; paid balance movement in `evidence/paid-a2a-private-lez.json` |
| One-command headless deployment | `scripts/deploy-agent.py`; `evidence/one-command-deployment.json`; `docs/DEPLOYMENT.md` |
| Separate owner Logos instance, no intermediary app server | `evidence/owner-channel-live.json`; `docs/OWNER.md` |
| Spending threshold | `evidence/above-threshold-live.json`; policy tests in `tests/test_engine.py` |
| All default skills | Registry in `commons_relay/skills.py`; `docs/SKILLS.md`; schema and adapter suites under `tests/` |
| A2A-compatible coordination | `docs/a2a-logos-binding-v1.md`; A2A tests; `evidence/a2a-free-task.json` |
| Autonomous paid agent task | `evidence/paid-a2a-private-lez.json`: private 3-unit payment and completed `program.query` on public LEZ testnet |
| Three illustrative use cases | File vault: `evidence/luna-live-storage-check.json` + `evidence/live-storage-share.json`; paid skill marketplace: `evidence/paid-a2a-private-lez.json`; multi-agent workflow: `evidence/multi-agent-workflow.json` |
| Three deployed role agents | `evidence/three-testnet-agents.json` and `evidence/module-load.json` |
| Public repo and docs | `README.md`, `docs/`, MIT + Apache-2.0 licenses |

## Usability

| Criterion | Current evidence |
| --- | --- |
| Third-party skill interface | `docs/SKILLS.md`; `commons_relay/external_skills.py`; extension tests |
| Basecamp owner interface | `native/ui/`; uses `Logos.Theme` and `Logos.Controls`; same-instance visual acceptance in `evidence/basecamp-ui.json`; owner flow in `docs/OWNER.md` |

## Reliability

| Criterion | Current evidence |
| --- | --- |
| Recover pending tasks after restart/network ambiguity | engine, Storage, Messaging, Wallet and A2A recovery tests |
| Failed owner delivery never becomes approval | notification retry/timeout tests; live held task in `evidence/above-threshold-live.json` |
| Skill failure isolation | service/bridge/adapter tests; native worker uses bounded concurrent dispatch |
| Payment is not duplicated after uncertainty | `tests/test_wallet_adapter.py`, A2A adapter tests, exact persisted transaction hashes |

## Performance

`docs/PERFORMANCE.md` records the quantities the pinned LEZ testnet actually exposes. The public executor does not currently return a finalized gas-used/CU-used receipt, so the document reports guest RISC0 user cycles, program byte size, confirmed blocks, and real proof wall time instead of inventing a fee number.

The successful public paid A2A proof is recorded in `evidence/paid-a2a-private-lez.json`.

## Supportability

| Criterion | Current state |
| --- | --- |
| Testnet deployment | Verified; sanitized public evidence committed |
| Standalone LEZ integration workflow | `.github/workflows/real-local-proof.yml` and `scripts/demo-local.py` |
| Core CI | `.github/workflows/core-tests.yml`; must be green on the submission commit |
| Clean local proof with `RISC0_DEV_MODE=0` | Script and workflow implemented; final submission should link the successful workflow run for the submission commit |
| README and deployment/owner instructions | Present |
| Narrated end-to-end video | Human recording still required by the prize rules before the solution PR is sent for review |

## Historical acceptance snapshot, before the reset

The sanitized evidence currently records:

- three role wallets shielded on public LEZ testnet with real proofs;
- one historical autonomous private 3-unit A2A payment, confirmed at block 42557;
- paid client balance 50 → 47 and provider balance 50 → 53;
- one above-threshold 6-unit request held under a 5-unit per-transaction limit with no wallet effect;
- encrypted owner-channel response;
- file upload/download/share, group messaging, and a two-provider multi-agent workflow;
- a fresh one-command headless deployment with inference disabled.

The submission video and final CI URLs should be added here immediately before opening the Lambda Prize solution PR.


## Current acceptance after the reset

Use the current evidence, not the earlier block-42557 receipt, for the deployed demo:

| Requirement | Current receipt and exact boundary |
| --- | --- |
| Independent shielded wallets and autonomous paid task | `evidence/current-paid-a2a.json`: 3 units at block 604, client 50 to 47 and provider 50 to 53; transaction rechecked on the current network. |
| Three illustrated use cases | `evidence/current-three-use-cases.json`: file vault, two-peer workflow and paid services marketplace. |
| Owner chat and custom skills | `evidence/owner-chat-ui-acceptance.json`, `evidence/custom-skill-ui-acceptance.json`, and `evidence/current-multiagent-ui.json`. |
| Spending control | `evidence/current-approval-ui.json`: 6-unit request held under a 5-unit automatic limit, then canceled with no wallet effect. |
| Real local proof CI and downloadable artifacts | Check the exact final release commit. Earlier successful CI does not automatically cover later source changes. |
| Builder narration and understanding | Required before submission; not supplied by an automated acceptance receipt. |

The marketplace request was owner-signed through Core, not initiated through a
model conversation. Its payment and provider execution occurred automatically
within policy; opening its result in Basecamp did not repeat the payment. The
file and multi-agent requests were initiated through chat and observed in Activity.
A live successful refund has not been demonstrated. Do not check that claim merely
because cancellation/refund unit tests exist.
