# Commons Relay

Owner-controlled task execution for Logos Core, with a native Basecamp interface.

CommonsRelay separates a replaceable planner from a durable task/permission engine
and trusted Logos adapters. Owners can authorize bounded goals rather than
approve every harmless step. Spending over the configured limit still waits for
a signed owner decision. A network timeout does not turn into a duplicate payment.

The native module and UI are implemented. The live Storage, Messaging, LEZ and
A2A adapters are in progress. This repository is not a completed LP-0008 prize
submission yet.

## Layout

- `commons_relay/engine.py`: signed tasks, goal grants, budgets and durable recovery.
- `commons_relay/service.py`: bounded local worker protocol used by the Core module.
- `commons_relay/file_crypto.py`: libsodium streaming encryption and sealed key sharing.
- `commons_relay/filesystem.py`: constrained file access and atomic output commit.
- `native/core/`: Logos Core plugin and worker lifecycle.
- `native/ui/`: Basecamp owner view using `Logos.Theme` and `Logos.Controls`.
- `adapters/`: planner and protocol adapter implementations.
- `tests/`: deterministic unit and local process tests.

Read [the architecture](docs/ARCHITECTURE.md) for the permission boundaries and
failure model. Test fixtures do not contact a model provider or move real funds.

## Tests

Python 3.11+, OpenSSL with Ed25519 support, and libsodium are required. No Python
packages need to be installed for the current core tests.

```sh
python3 -m unittest discover -s tests -v
```

When libsodium is not in the standard library search path, set
`COMMONS_RELAY_TEST_SODIUM` to an explicitly trusted installed library. The development
build uses the library already supplied with Logos Basecamp.

## Native build

Use the pinned Logos dependency checkouts and Qt 6.9 development files:

```sh
cmake -S native -B out/native -G Ninja \
  -DLOGOS_DEPS_DIR=/path/to/logos-dependencies \
  -DLOGOS_VIEW_GENERATOR=/path/to/logos-view-generator \
  -DCMAKE_PREFIX_PATH=/path/to/qt
cmake --build out/native -j2
cmake --install out/native --prefix out/modules
```

The Core plugin bundles its local worker, not an inference model. It accepts
agent profiles beneath `COMMONS_RELAY_ALLOWED_STATE_ROOT`; the native owner app can
use `COMMONS_RELAY_DEFAULT_PROFILE`. Profiles are created using
`scripts/create-profile.py` with separate new owner and agent directories.

Source is dual-licensed under MIT and Apache-2.0. Development is AI-assisted.
Do not put real funds in development profiles.
