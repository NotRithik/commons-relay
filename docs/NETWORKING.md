# Connect agent deployments

A discovery topic is a name within a Messaging network, not a way to join that
network. Two disconnected nodes can both use `commons` and still never hear each
other. The Relay native module configures the official Logos Delivery module;
it does not replace Delivery with HTTP, a central directory or a custom broker.

## Official network preset

New deployments default to `logos.dev`, with the reviewed Delivery module's named
preset. The module controls its bootstrap records. Reachability, NAT and firewall
conditions still need to be checked on the actual host. This setting is distinct
from the LEZ testnet endpoint used by the wallet and the Logos Storage network.

## One-machine development mesh

The original development mesh binds to `127.0.0.1`. Its first node has an empty
entry-node list; others bootstrap using its stable Delivery peer address. This is
useful for local development, but a second computer cannot reach that loopback
address. It must not be described as a two-machine demonstration.

## Explicit private-LAN mesh

LAN mode is an operator choice, not a setting that a discovered agent may change.
It binds the Messaging TCP listener to all interfaces and advertises the private
IPv4 address chosen by the operator. The accepted addresses are RFC 1918 ranges;
ports must be 1024 to 65535, and bootstrap lists contain at most eight peers.
REST, administrative HTTP, WebSocket and metrics listeners remain disabled.

Example provider `delivery.json`, in the protected agent profile:

```json
{
  "mode": "lan",
  "advertiseAddress": "192.168.1.35",
  "tcpPort": 34413,
  "clusterId": 42,
  "entryNodes": [
    "/ip4/192.168.1.10/tcp/34410/p2p/REPLACE_WITH_THE_ACTUAL_DELIVERY_PEER_ID"
  ]
}
```

Replace the example IPs and peer ID with the actual host addresses. For the first
node, an empty `entryNodes` list is valid. Use the same cluster and topic for all
participants. A peer ID identifies the Delivery node, not its owner authorization
key or public A2A service address.

For a new deployment, `scripts/deploy-agent.py` accepts `--lan-address` and repeated
`--lan-peer` arguments, together with the ordinary deployment and service flags.
`--local-cluster-id` selects the development cluster. LAN and local-bootstrap
options cannot be mixed. Existing deployments require a planned idle-agent
configuration update, not a second wallet/deployment creation.

Allow only the necessary Messaging TCP port through the host firewall. On WSL 2,
the Linux instance may be behind Windows NAT. An outbound connection to a LAN
seed may work while an inbound connection to the provider does not. Check actual
listeners and bidirectional delivery rather than assuming that an advertised
Windows IP is reachable. Any Windows forwarding rule should be limited to the
required port and trusted local network; do not expose management APIs.

## What to verify

First check the exact Core session and module health. Then check Delivery's bound
port and stable peer address, connect the nodes, and send a discovery query. Verify
that a signed card arrives on the other machine, invoke a free task, and observe
the matching task IDs and result in both journals. Only after that should a paid
flow be used to verify LEZ settlement.

The LAN mode has been compiled locally. A compiled option is not a successful
Windows/WSL or two-machine acceptance receipt. Record those separately, including
which machine was the client, provider, owner interface and prover.
