# A2A 1.0 over Logos Messaging

This binding carries the A2A 1.0 data model over Logos Delivery instead of an HTTP
server. Agent Cards advertise `protocolBinding: LOGOS-MESSAGING`, a `logos://`
endpoint and `protocolVersion: 1.0`. An ordinary HTTP A2A client needs this
transport adapter; the repository does not claim direct HTTP interoperability.

Agent Cards use detached EdDSA JWS signatures over a canonical JSON document,
following the `AgentCardSignature` shape in A2A 1.0.1. The canonicalization helper
accepts the RFC 8785 safe-integer subset; floating-point card fields are rejected.
Known contact signing keys are pinned by the owner. A self-signed advertisement
is not enough to replace a contact key or authorize spending.

## Binding frames

Each RPC request is a JSON-RPC 2.0 object containing `jsonrpc`, `id`, `method` and
`params`. Supported methods are `SendMessage`, `GetTask`, `CancelTask`,
`SubscribeToTask` and `GetExtendedAgentCard`. The object is placed in an
`a2a-request` message. Responses are `a2a-response` messages containing the
matching request ID, a result or a JSON-RPC error. Sender identity is authenticated
by the signed envelope before dispatch. Recipient topics contain sealed
ciphertext, not plaintext requests or results.

Messages and tasks use the 1.0 names: `messageId`, `contextId`, `taskId`,
`ROLE_USER` / `ROLE_AGENT`, and `TASK_STATE_*`. Input and output are JSON Data
Parts. A new service request's Data Part contains `skill`, `arguments` and a fresh
`refundAddress`. The last field belongs to the advertised payment extension.

A subscription first returns a `StreamResponse` containing `task`. Later
`a2a-event` messages carry `{requestId, sequence, response}`, where `response` is
a standard `StreamResponse` containing `statusUpdate` or `artifactUpdate`. The
binding sequence is outside the A2A object. It is persisted and deduplicated;
a lost transport message is retransmitted with the same signed message ID.
`GetTask` can recover the complete current state and result after an interruption.

The internal `unknown` effect state maps to A2A `TASK_STATE_WORKING`, with
reconciliation details, rather than inventing an A2A terminal state.

## Publication and discovery

Signed public Agent Cards are stored through the actual Logos Storage module.
Their content addresses accompany announcements on a Logos discovery topic.
Known peers also receive an encrypted announcement so late joiners can discover
services without relying on one transient broadcast. Discovery advertises
capabilities; the owner-pinned contact key controls whether a peer is trusted.

## Owner authority versus exported services

A local owner may use the full installed skill set. A remote requester may invoke
only the services explicitly listed in `services.json`. The internal service
entry point rejects wallet transfers, arbitrary program execution/deployment,
configuration changes, and any skill that would spend the owner's funds. Remote
service prices are separate from the provider's zero-spend execution authority.

## Reference

A2A v1.0.1 protobuf: https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto
