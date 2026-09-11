# Use a Kite service from another Python application

`commons_relay.a2a_client.LogosA2AClient` is a framework-neutral transport adapter.
It accepts and returns the A2A 1.0 JSON messages used by our Logos binding. A Python
agent framework can call it directly, or wrap its methods as framework tools.

It is not an HTTP gateway. Run a local Logos Core session with the Relay module
and Messaging configured, then point the client to that existing session. The
adapter uses local Core IPC; the Core module sends signed, encrypted messages to
the provider. No intermediary web server or wallet key is supplied to the adapter.

```python
from commons_relay.a2a_client import LogosA2AClient, PendingRequest

client = LogosA2AClient('/path/to/logosctl', '/path/to/core-session')
providers = client.discover('commons', wait_seconds=5)
for entry in providers:
    print(entry['address'], entry['card']['name'])
    print([skill['id'] for skill in entry['card']['skills']])
```

The returned card is the original signed JSON document. Do not reserialize it
through a schema library that discards explicit default fields before checking
its signature. `client.import_card(card)` verifies and remembers a public card
without adding it to the owner's trusted address book.

## Request a free service

Persist the request ID and exact input before sending. A timeout is not permission
to create another request. The same ID and input can be re-enqueued; changed input
under the same ID is rejected by the Core journal.

```python
request_id = 'my-app-request-0001'  # Save with your business operation.
peer = providers[0]['address']    # Select by the required advertised skill.
client.send_free_task(peer, 'meta.skills', {}, request_id=request_id)
try:
    accepted = client.wait(request_id, timeout=30)
except PendingRequest:
    # Keep the ID; call wait(request_id) again when the network returns.
    raise

task = accepted['task']
# The accepted task may still be working. Query the same remote task, not a new job.
client.request(peer, 'GetTask', {'id': task['id']}, request_id='get-request-0001-01')
current = client.wait('get-request-0001-01')
print(current['status'])
```

`send_free_task` rejects a nonzero advertised price and never submits a payment.
It is a convenience, not a general paid-service API. Other A2A operations are
available through `request(peer, method, params, request_id=...)`, including
`GetTask`, `ListTasks`, `CancelTask`, `SendMessage`, `SendStreamingMessage`,
`SubscribeToTask`, and `GetExtendedAgentCard`.

## Paid services

The owner-authorized `agent.task(agent_address, skill, params)` skill remains the
payment path. It checks the signed card, binds the quote to the exact request and
recipient, reserves the spend under the configured limits, makes one LEZ payment in the explicitly reviewed mode, and reconciles that exact
transaction. Existing requests default to private; public payments use separate
public funds, must be advertised by the provider, and require explicit owner
approval. A client never silently switches modes or converts private funds. The native Services screen signs
the price reviewed by the owner; a price change is rejected. A planner uses the
same engine under its signed goal grant and budget.

A generic A2A client can negotiate the advertised payment extension with
`SendMessage`, but possession of a public card does not confer permission to spend
its local agent's wallet. See `a2a-payment-v1.md` for the extension. A paid result
must not be reported as complete merely because a quote or payment hash arrived.

## Streaming and recovery

Start with `send_free_task(..., streaming=True)`, then:

```python
for event in client.stream(request_id, timeout=120):
    save_cursor = event['sequence']  # Persist after handling each event.
    print(event['response'])
# Resume the same subscription with stream(request_id, after=save_cursor).
```

The outer sequence belongs to the Logos binding. The value in `response` is an
A2A StreamResponse. The initial task snapshot may have a sequence greater than
one. Missing events are not skipped; the client waits for the durable transport
retry. `GetTask` can recover current state independently. A completed task rejects
a new subscription, but remains available via `GetTask`.

## Scope of compatibility

This provides the custom transport explicitly required by LP-0008. It does not
make an unmodified HTTP-only A2A client or a stock LangChain/Vertex connection
speak Logos Messaging. It is not an independent A2A TCK certification. The runtime
continues to enforce peer-isolated task access and owner/service authority
separation regardless of the client framework.

The compiled Core module must include the same version of the client IPC methods
as the Python adapter. `METHOD_NOT_ALLOWED` means the loaded native module is too
old or the operation is not exposed; restarting only the Python client cannot
change the native module's allowlist.

## Optional LangChain example

`adapters/langchain/service_pipeline.py` composes discovery, a free A2A task and
result handling as ordinary LangChain runnables. Its journal preserves the same
remote task across restarts and its CLI disables external tracing. See that
adapter's README for setup and the explicit limits of the example. A source
example is not an interoperability result until a live run is recorded.
