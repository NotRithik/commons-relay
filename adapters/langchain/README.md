# LangChain over Logos A2A

This is an optional client adapter, not a second wallet or an HTTP gateway. It
composes three actual LangChain `RunnableLambda` stages: discover a public
provider, run its free capability-list service, and summarize the returned A2A
artifact. It calls the same documented Logos transport as the other Python
client example. No model call or private key is required.

From the repository, create a separate environment and install the pinned client
library. Do not install it into the agent daemon or MCP bridge environment.

```sh
python3 -m venv .venv-langchain
.venv-langchain/bin/python -m pip install -r adapters/langchain/requirements.txt
.venv-langchain/bin/python adapters/langchain/service_pipeline.py \
  --logosctl /path/to/logosctl \
  --session /path/to/already-running-core-session \
  --provider-name 'Blockchain Agent' \
  --topic commons \
  --request-id langchain-example-0001 \
  --journal /path/to/private-state/langchain-example-0001.json
```

Use the Core `TMPDIR` recorded by your deployment when its endpoint namespace
requires it. The client and provider must share a Messaging network and discovery
topic. The provider must explicitly offer `meta.skills` for zero units. The
example will refuse a nonzero advertised price and has no payment method.

A request ID and journal belong to one business operation. Re-run with the same
arguments to resume it. An uncertain result is not permission to create another
request ID. The remote task ID and its context are checked on every status poll;
only an actual completed artifact becomes the final capability summary.

The CLI disables LangSmith tracing with both environment configuration and a
scoped `tracing_context(enabled=False)`. It does not upload prompts, results or
service metadata to a tracing backend. An application that deliberately enables
such tracing must separately explain that data sharing to its users.

The reusable `make_pipeline` function returns a normal LangChain runnable. This
example intentionally has no LLM planning loop: it isolates transport/framework
interoperability from model behavior and payment. The native Kite chat workflow
is the independent demonstration of LLM-driven discovery, service selection and
owner-authorized payment.

## What this does not establish

An unmodified HTTP A2A client still needs a Logos transport adapter. A completed
free capability request is not evidence of a paid task, a Windows-hosted Exa
search, or a model's reasoning quality. Execution receipts must identify which
client, provider and framework version actually ran.

Framework references:
- https://reference.langchain.com/python/langchain-core/runnables/base/RunnableLambda
- https://docs.langchain.com/langsmith/conditional-tracing
- https://pypi.org/project/langchain-core/1.6.2/
