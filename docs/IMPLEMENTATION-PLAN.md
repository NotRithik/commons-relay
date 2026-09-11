# Kite / LP-0008 completion plan

Updated 2026-09-11 after rereading the official LP-0008 Success Criteria, Submission Requirements and repository Evaluation Policies. This checklist records observed work, not submission clearance. Existing task IDs are inspected rather than replayed. The official requirement checklist remains in PRIZE-CHECKLIST.md.

## Completed and observed

- [x] Native Logos Core agent and separate encrypted Basecamp owner channel.
- [x] Public service discovery is separate from owner-trusted contacts.
- [x] Hash-pinned third-party extension installed without modifying the core module.
- [x] Autonomous private payment to a publicly discovered text-statistics provider, confirmed at block 3206; no owner click on the payment. See evidence/paid-public-extension-20260911.json.
- [x] Windows Exa request initiated through chat, paid 1 private testnet unit at block 3359, with matching client/provider results. Its truncated response contained two linked excerpts. See evidence/chat-windows-exa-verified-20260911.json.
- [x] Chat file-vault round trip: authenticated download, 222 bytes, identical SHA-256.
- [x] Chat delegates to two agents and combines their actual zero-cost results.
- [x] Default program.deploy, wallet.send and corrected program.call confirmed through Basecamp at blocks 3639, 3703 and 3711 respectively. See evidence/default-wallet-program-ui-20260911.json.
- [x] Create Agent UI installed; Research assistant recovered with the same identity, no wallet operations/model calls/public listing, and a successful free meta.skills task. Unused duplicate reviews created no wallets. See evidence/create-agent-recovery-ui-20260911.json.
- [x] Its complete formatted 8,650-character saved result was scrolled in Basecamp without another task.
- [x] Local creation, saved owner connections and public provider discovery have distinct UI controls and documentation; remote setup remains an explicit CLI operation.
- [x] Real standalone private proof completed with RISC0_DEV_MODE=0 and verified balance 5. The earlier 'still running' entry was stale. Existing prerequisites were reused; a clean final-version CI run remains separate. See evidence/local-current-wallet-proof-20260911.json.
- [x] Exa, text statistics and JSON-formatting services installed and published on Messaging with reviewed 1-unit prices and both public/private payment choices.
- [x] Public Exa discovery and request initiated through Basecamp chat. Task 2679cd5aa50049e4a39a3bdcaa42631a completed; 1 public testnet unit confirmed at block 3914; provider/client results match. Total task time was 121 seconds, including 64 seconds awaiting approval. See evidence/public-exa-chat-20260911.json.
- [x] Fixed recovery of that saved approval after a legitimate 30-second deadline reduction. Exact intent, inputs, price and signature binding remain checked; deadline extension is rejected. The existing conversation now reads Completed with no binding error, without another search/payment.
- [x] Installed clickable named tool rows with result summaries and a final receipt-derived response after the tools. The saved Exa result was opened in the actual UI. This response is not represented as a newly generated model synthesis.
- [x] Automatic INSTRUCTIONS.md notices enabled in the normal MCP launchers and received in this conversation. No further notifier work is on the prize critical path.

## Current acceptance work, in order

- [x] Paid invalid-JSON formatting request failed and its full public refund was independently verified by the client; reservation released. The provider remained online and the next free meta.skills UI task completed. See evidence/public-failed-service-refund-20260911.json.
- [x] Actual discovery overlapped the installed formatter failure; event order/timestamps show the failure wholly inside the successful discovery task. See evidence/concurrent-failure-isolation-20260911.json.
- [x] Canceled an accepted unpaid A2A task through the default agent.cancel in Basecamp; no payment or provider execution occurred. The separate paid-failure full-refund case is also verified.
- [x] Provider restart preserved the same pending task, exact quote and service identity. Its owner channel reconnected, then the task was canceled without payment. See evidence/pending-restart-cancel-ui-20260911.json.
- [ ] Finish UI result scrolling and multi-page navigation. The first search-response scrolling attempt exposed blank content; the outer/inner scrolling fix is not yet accepted.
- [ ] Install and verify stable chat/task rows so live refresh does not invalidate clicks or keyboard focus. StableRows changes are written but were not in the last installed build.
- [ ] Verify actual intermediate model commentary and a useful post-tool final answer in a new bounded conversation. Source is written and staged; a receipt-derived fallback must not be called a model-generated summary.
- [ ] Verify the Notes assistant recovery and model-configuration bootstrap separately; do not infer them from Research assistant acceptance.
- [ ] Reconcile the old malformed Blockchain program.call safely. Its transaction is 2a269d037920d57049f0777f7f81c292e9c5fde1b4a92b159447053fff2cbfac. Do not relabel it as the corrected successful Storage call or blindly resend it.
- [x] All 21 required default skills have registered implementations, documentation and completed role-journal records. Independent discovery survived a simultaneous extension failure. No batching or concurrent wallet writers are claimed.
- [x] Current README/provider/owner/security guides and compute-cost reporting reviewed; upstream source confirms no metered CU receipt. Public failure/refund behavior and available measurements are documented.
- [ ] Publish clean final source and matching loadable packages, excluding credentials, wallet profiles and private diagnostics.
- [ ] Run real standalone integration in CI and verify green CI on the exact final default-branch commit; green review-branch CI alone is insufficient.
- [ ] Final UI walkthrough and reproducible evaluator instructions.
- [ ] Builder-narrated video covering at least three complete use cases and the real proof terminal, with RISC0_DEV_MODE=0 visible. Video is last.
- [ ] Owner confirms eligibility, code rights and submission terms before the solution PR. Do not submit an unfinished entry or claim prize acceptance.

## Operational boundaries

Keep existing identities and pending transactions. No repeated Exa payment and no new CUDA build. Acceptance is live Basecamp interaction and manual/printf observations; CI checks do not replace it. Give frequent MCP terminal updates. For an explicitly blocked operation, provide the precise manual command and save its output for reading while continuing unrelated work. Do not bypass a safety rejection. Stop live changes only when their state cannot be observed reliably. Windows shutdown, if still requested, requires first checking its remaining tasks/services and saved evidence.
