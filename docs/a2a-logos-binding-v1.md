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
`params`. Supported methods are `SendMessage`, `SendStreamingMessage`, `GetTask`,
`ListTasks`, `CancelTask`, `SubscribeToTask` and `GetExtendedAgentCard`. The object is placed in an
`a2a-request` message. Responses are `a2a-response` messages containing the
matching request ID, a result or a JSON-RPC error. Sender identity is authenticated
by the signed envelope before dispatch. Recipient topics contain sealed
ciphertext, not plaintext requests or results.

Messages and tasks use the 1.0 names: `messageId`, `contextId`, `taskId`,
`ROLE_USER` / `ROLE_AGENT`, and `TASK_STATE_*`. Input and output are JSON Data
Parts. A new service request's Data Part contains `skill`, `arguments` and a fresh
`refundAddress`. The last field belongs to the advertised payment extension.

`SendStreamingMessage` immediately returns an initial `task` and starts its
stream. `SubscribeToTask` attaches to an ongoing task and returns the current
`task`. A terminal task rejects a new subscription. Later
`a2a-event` messages carry `{requestId, sequence, response}`, where `response` is
a standard `StreamResponse` containing `statusUpdate` or `artifactUpdate`. The
binding sequence is outside the A2A object. It is persisted and deduplicated;
a lost transport message is retransmitted with the same signed message ID.
`GetTask` can recover the complete current state and result after an interruption.
The sender waits for acknowledgment of the initial Task response before queuing
stream events. Each event is matched to its original request, task and context.
After the terminal events enter the durable outbox, the subscription is removed;
the outbox continues delivery independently.

Push notifications are not advertised. Their four configuration operations return
`PushNotificationNotSupportedError`; no public webhook is started.

The internal `unknown` effect state maps to A2A `TASK_STATE_WORKING`, with
reconciliation details, rather than inventing an A2A terminal state.

## Listing tasks

`ListTasks` returns only tasks belonging to the authenticated requester. Its
`totalSize` uses the same peer and filters. Supported filters and options are
`contextId`, `status`, `statusTimestampAfter`, `pageSize`, `pageToken`,
`historyLength` and `includeArtifacts`. The page-size default is 50, with an
accepted range of 1 to 100. A page can be shorter to fit the bounded transport.
Artifacts are omitted unless requested. No message history is retained, so
responses contain no history messages even when a maximum is requested.

Results are ordered by update time descending, then task ID. Continuation tokens
are authenticated, bound to the requesting peer and filters, and survive agent
restarts. Concurrent updates can move a task ahead of an existing cursor; start
a fresh listing to refresh the newest state. Nonempty tenant routing is rejected
because the advertised Logos interface has no tenant.

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

## Verification boundary

Local regression tests cover the published A2A v1.0.1 field definitions,
peer-isolated pagination, and signed encrypted streaming exchanges. This is not
a claim of passing the independent A2A TCK or supporting an unmodified HTTP-only
client. The custom transport adapter is required.

## Reference

A2A v1.0.1 protobuf: https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto
