# Kite: complete demonstration runbook

**Status: preparation guide, not final submission clearance.** Finish the open
items in PRIZE-CHECKLIST.md and verify the source/assets/CI commit before recording
the final video. A recorded task can be inspected without sending it again.

## Before recording

Keep one Basecamp window open with Kite selected. Confirm the owner dropdown has
the intended existing profiles and each shows a fresh **Agent online** response.
The role names are independent identities; they are not hardcoded limits on an
agent's tools. Keep the Windows Exa provider running on the same Messaging mesh
and the `commons` topic. A public listing is not proof that a provider will still
be reachable when a task starts.

Use disposable testnet wallets only. Hide `.env`, owner private keys, wallet
storage, private witness files, bearer tokens and raw private proof logs. Do not
show the full MCP launch log or the computer's hardware serial number. The
sanitized evidence JSON files and ordinary task views are the recording sources.

Do not interrupt a proof or resend a paid prompt just to improve the recording.
Record its real start and completion; shorten the wait in editing and label that
cut. Show the actual proof-generation terminal and `RISC0_DEV_MODE=0`, not merely
an unrelated command that prints the string. A saved receipt must be identified
as a receipt, not presented as a new live transaction.

## 1. Show the product and authority boundary

Click **Kite**, choose **My agent**, and wait for **Agent online**. Show the four
areas: **Chat**, **Activity**, **Skills & tools**, and **Services**. In settings,
show the selected model/provider and the testnet spending threshold, without
opening its API key.

Explain: the language model proposes work; the signed task engine decides what
is authorized, and the wallet/network receipts decide whether it completed. An
agent can pay below its owner-set threshold without an additional payment click.
Above the limit it must ask. A public provider is not an owner.

## 2. Discover the provider from chat

With actions off, send:

> Find the agents on the commons topic. Tell me what Windows Exa Search offers,
> its price and required inputs. Only discover; do not request work or pay.

Wait for the linked discovery tool to complete. Open its **View details** entry
and show Windows Exa Search, its public `relay-...` identity, the advertised skill
and price. This step must not be narrated as a completed search or payment.

The recorded discovery and task use `exa.search_free` at one LEZ-testnet unit.
“Free” refers to the provider's upstream Exa MCP plan, not the fee charged by this
agent. Explain the distinction rather than calling the agent request free.

## 3. Ask the agent to hire the discovered service

For a new, deliberately authorized recording run, send this goal through Chat:

> Discover the agents on the commons topic, then ask Windows Exa Search to search
> for official Logos documentation about decentralized storage and encrypted
> messaging. Request 3 results. Use its listed exa.search_free service, spend at
> most 1 LEZ-testnet unit total, and summarize the actual returned results with
> their links. Ask me first if approval is needed. Do not create another paid task
> if the first one takes a while.

With actions off, open **Review action requested by agent**. Show the exact
provider address, service, query, result count and total one-unit cap. Check the
review checkbox and choose **Approve** only after verifying those values. This
is authorization of the task; do not imply that the owner clicked a later wallet
payment button when none was required.

The current task `c248909274dc4882a3011cd9978efe04` is already authorized and was
proving when this guide was written. **Inspect that task; do not resend its prompt.**
Only call it completed after its own on-chain payment and returned Exa artifact
have been checked.

During the wait, open **Activity** or the linked **View details**. Show that the
task keeps the same ID and reports private proving, payment confirmation and
provider execution as distinct stages. “Nothing sent yet” applies only before
broadcast; an uncertain network result must not be called an unpaid failure.

After completion, inspect the saved result, paid amount, transaction hash and
returned search links. Show the Windows provider's corresponding A2A task and
upstream result using sanitized records. A request reaching the provider, a
quote, or a proof start is not enough. The result must be the one attached to the
same paid task. Distinguish a receipt-derived preview from a model-written
summary; the current long-task UI may require a separate read-only summary turn.

## 4. Show open provider onboarding

Open **Services > Find a service** on the client and refresh `commons`. Public
cards should be described as signature-checked, not as owner-trusted or endorsed.
Show the provider's public address and exact service price.

Choose the provider profile, then **Services > Offer a service**. Explain the
listing name, description, topic, service selection, price and explicit public
switch. Open the listing review to show what would be advertised. Do not save
an unnecessary change during the recording. A new provider publishes only the
selected service metadata; it does not publish its private files or API key.

Show the small custom-service manifest/executable and `scripts/install-skill.py`.
Explain that an operator installs reviewed code and pins its hash, then publishes
it through the listing editor. No core-module edit is needed to add the behavior.
An installed extension can earn a fee without gaining wallet-debit permission.

The earlier paid custom-extension task `ee383a53a13e43258e878116055365dd` is a
separate completed example: one unit at block 3206, returning 9 words and 54
characters. Both endpoints were on the Mac. Do not substitute that result as
proof that the Windows Exa task completed.

## 5. Show the file vault through chat

Use a synthetic file already placed in the selected agent's **inputs** folder.
For the recorded sample, the selected profile was **Blockchain peer**. The same
registry supports Storage tools on that instance; the profile name is not a
hardcoded restriction.

The upload path is relative to **inputs**: `kite-vault-20260911.txt`, not
`inputs/kite-vault-20260911.txt`. The restored filename is relative to **outputs**:
`kite-vault-restored-20260911.txt`, not `outputs/...`. Do not overwrite an existing
output. For a genuinely new run, use a new synthetic source/output filename.

Open the existing upload and download results. The recorded content address is
`zDvZRwzm4vdebVRBmyuSBx8DyRKWyVKnX2qRa8beUxX1GribaDYs`. The restore was 222 bytes,
with authenticated decryption and the same SHA-256 as the original. Show the
comparison receipt without displaying unrelated private files. Note that the
first incorrect path was rejected before an upload; do not edit the recording to
claim that incorrect input worked.

## 6. Show spending controls and durable recovery

Open the recorded above-threshold review from the appropriate existing profile.
Explain the six-unit proposal under a five-unit automatic limit, its pending
approval state and its canceled outcome. That record proves a held request; it
is not evidence of a successful six-unit payment or refund.

For restart recovery, use the separate unfunded Linux deployment receipt and its
same-identity restart receipt. Do not restart the agent currently proving a paid
task. Show that the paid/free completed records remained intact after inbox
recovery and that the saved A2A stream resumed from its original cursor.

For a fresh failure demonstration, use an explicitly harmless, zero-spend request
with an unavailable input or an offline free peer. Show the bounded error and
that the app remains usable. Do not force an uncertain paid request to fail by
killing its wallet process or deleting its journal.

## 7. Close with reproducibility and precise limits

Show the repository's deployment command, default-skill list, provider SDK,
owner guide, transport binding and security model. Show the exact release commit
and its own passing required CI/standalone proof workflow. Earlier green CI on
another commit is not a matching check of this working tree.

The three required demonstrations can draw on the completed file vault, paid
service marketplace and multi-agent workflow evidence, provided their actual
results are shown. Cross-framework client integration is a separate optional
example; only include it as completed once its own live run is recorded.

Say which machines played each role: Basecamp owner, client agent, provider and
prover. The tested Windows connection uses an explicit private-LAN Messaging
mesh. It is not evidence that arbitrary disconnected internet nodes discover one
another. No GPU performance claim should be made without a verified GPU proof
and a measured comparable workload.

Finish by stating the actual remaining limitations. Do not describe the software
as audited, production-ready, or already accepted by the prize evaluators.
