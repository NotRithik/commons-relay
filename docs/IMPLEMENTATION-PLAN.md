# Kite / LP-0008 completion plan

Updated 2026-09-11. This is a working checklist, not submission clearance. Checkmarks mean the stated observation exists; source or a build alone is not end-to-end acceptance. Existing task IDs are inspected rather than replayed.

## Verified functional milestones

- [x] Native Logos Core agent and separate encrypted Basecamp owner channel.
- [x] Public service discovery separate from owner-trusted contacts.
- [x] Hash-pinned third-party extension installed without editing the core.
- [x] Windows Exa task requested through chat, paid 1 unit, returned matching data on both machines; transaction confirmed at block 3359. Upstream response was truncated and contains two linked excerpts, not three complete results.
- [x] Readable titles, URLs and truncation warning shown for that saved Exa result in Basecamp; no repeated search/payment.
- [x] Chat file vault round trip: authenticated download, 222 bytes, identical SHA-256.
- [x] Chat delegates to two agents and combines both actual zero-cost results.
- [x] Default program.deploy confirmed at block 3639.
- [x] Default wallet.send confirmed at block 3703.
- [x] Corrected default program.call submitted and approved in Basecamp, confirmed at block 3711. Public-call preflight checks program identity, execution and zero-debit limit.

## Current work, in order

- [ ] Install the already-built Create Agent UI, then create one unfunded local agent through its review screen; verify identity, owner connection, no model/funding/public listing, and repeat-click/reopen behavior.
- [ ] Exercise scrollable saved JSON and result paging in Basecamp using a long existing result; verify complete content remains accessible without new tasks.
- [ ] Make local creation, owner connections and public-service discovery distinct and understandable. Document the remaining remote-CLI setup boundary.
- [ ] Finish safe scheduling improvements: independent bounded read work must not wait for a private payment proof. Do not run concurrent wallet writers against the same notes/nonces. Do not claim batching without implementing and validating it.
- [ ] Reconcile the old malformed Blockchain program.call safely. It must not be relabeled as the corrected Storage call or blindly resent. Its hash remains 2a269d037920d57049f0777f7f81c292e9c5fde1b4a92b159447053fff2cbfac.
- [ ] Recheck reliability/failure isolation and all required default-skill evidence against the exact official LP-0008 checklist.
- [ ] Update compute-cost documentation, reproduction scripts, provider/owner guides and known limitations from actual measurements.
- [ ] Review and publish a clean source commit with matching loadable assets. Keep keys, wallets, runtime profiles and private diagnostics out of the repository.
- [ ] Run the real standalone sequencer demo with RISC0_DEV_MODE=0 and verify required CI on that exact default-branch commit. Manual UI acceptance remains separate from unit tests.
- [ ] Final UI walkthrough and demo instructions.
- [ ] Builder-narrated video and explicit eligibility/submission confirmations (video last).

## Operational boundaries

Keep existing identities and pending transactions. No duplicate Exa payment, no new CUDA build. Check INSTRUCTIONS.md regularly and send progress through the MCP terminal. If a tool action is explicitly blocked, report that precise action and continue unrelated work. Do not bypass a safety rejection through another tool. Shut down the Windows host only after confirming its services/tasks and required receipts are no longer needed, as requested by the owner.

## Latest observations

- [x] Research assistant same-identity recovery, live owner connection and first free meta.skills task in Basecamp; no model calls or wallet operations. See evidence/create-agent-recovery-ui-20260911.json.
- [x] Scrolled its full formatted 8,650-character saved result in the UI. Multi-page navigation remains a separate check.
- [ ] Notes assistant fresh startup revealed native-IPC readiness timing; fix written, same-profile recovery pending.
- [ ] New-agent Pi configuration bootstrap written; activation awaits the requested manual command.
- [ ] Current wallet real standalone proof is running as job ff9681acfc624b24. Do not start another local demo on its port.
