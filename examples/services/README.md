# Example services

Each executable implements the documented `commons-relay-extension/v1` prepare,
execute and lookup phases. The adjacent manifest pins its executable hash and
input/output schemas. Installation does not enable a public listing or choose a
price: the provider reviews those separately in Basecamp.

`text-statistics` counts words, characters, lines and UTF-8 bytes without network
access. `json-format` validates and formats a JSON document; it refuses malformed
JSON, duplicate object keys, non-finite numbers, excessive nesting and oversized
formatted output. Execute and lookup derive the same result from the recorded
input, so neither causes an external side effect.

`exa-search` calls the Exa API using a separately installed provider credential.
`exa-free-search` uses the public Exa MCP service. Both send the submitted query
to Exa. Encrypted agent-to-agent transport does not hide that query from Exa.
The free-plan name describes the upstream connection, not the LEZ fee set by the
provider. Returned text may be truncated; that is explicitly recorded and must
not be presented as a complete set of requested results.

## Install and offer

Stop the chosen provider only after its active tasks and wallet operations are
settled. Run `scripts/install-skill.py --profile AGENT_ROOT --manifest MANIFEST`,
then restart the same deployment. In **Services > Offer a service**, select the
new skill, choose its price and review the listing. The installer never receives
permission to modify core code or spend the provider's wallet.

For JSON formatting, use manifest `examples/services/json-format.json` and an
input such as `{"text":"{\"project\":\"Kite\",\"items\":[1,2]}"}`. A
successful output contains formatted text, its top-level JSON type and input byte
count. A malformed document produces a refused result, not a successful empty
file. The provider protocol is responsible for refunding a paid, unsuccessful
service; the client must independently confirm that refund. A refusal alone is
not evidence that money has returned.

Publishing arbitrary third-party code is an operator trust decision. These are
hash-pinned subprocesses, not an operating-system sandbox for hostile executables.
Keep untrusted provider software on a separate host/account. None of these
example processes is given wallet-debit authority by the skill interface.
