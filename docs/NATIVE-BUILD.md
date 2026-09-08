# Build and package the native Basecamp components

The owner interface is a real Qt Remote Objects module. Its task/agent transport
runs in Logos Core; a webpage or mocked screenshot does not exercise that boundary.
The reviewed local build uses macOS arm64, Basecamp 0.2.3 and Qt 6.9.2. Linux is an
available CMake target, but a macOS acceptance receipt does not establish Linux UI
acceptance.

## Prerequisites and pinned inputs

Install Python 3, git, CMake 3.20+, a C++17 compiler, libarchive's `bsdtar`, curl,
and the nlohmann JSON C++ headers. The three official Logos checkouts are pinned
by `scripts/fetch-native-deps.py`. Exact official Qt downloads are pinned by URL,
size and SHA-256 in `native/qt-archives.json`. Inspect the small fetchers before
using them; they do not execute downloaded installers.

From a clean checkout:

```sh
python3 scripts/fetch-qt.py "$PWD/.build-deps/qt"
python3 scripts/fetch-native-deps.py "$PWD/.build-deps/logos"
export QT_PREFIX="$PWD/.build-deps/qt/prefix"
export LOGOS_DEPS_DIR="$PWD/.build-deps/logos"
export COMMONS_BUILD_JOBS=2
/bin/sh scripts/build-native.sh
```

Fetch and compile are separate. `build-native.sh` itself does not need outbound
network access. `RELAY_NATIVE_BUILD_DIR` and `RELAY_NATIVE_INSTALL_DIR` override
the local output directories; installation does not default to `/usr/local`.
The outputs are `commons_relay_wallet`, `commons_relay_module`, and
`commons_relay_owner_ui`. Only this project's code is installed.

The Rust wallet companion is a separate executable. Build it using the pinned
Rust and proof dependencies documented in `DEPLOYMENT.md` and `PERFORMANCE.md`.
Python, OpenSSL and libsodium must be available to the Core worker. Optional
language-model chat additionally uses the locked Pi adapter dependencies. Neither
native compilation nor opening the UI starts a paid model request.

## Create LGX packages

Use `liblgx` from a trusted Basecamp installation or a reviewed official build.
For each staged component:

```sh
python3 scripts/package-lgx.py \
  --lgx-lib /path/to/trusted/liblgx.dylib \
  --native-dir "$PWD/out/modules/commons_relay_wallet" \
  --module commons_relay_wallet --variant darwin-arm64 \
  --output "$PWD/out/packages/commons_relay_wallet.lgx"
```

Repeat for `commons_relay_module` and `commons_relay_owner_ui`. The official LGX
library verifies each candidate before the output is created. The script refuses
to overwrite an existing package. It includes only reviewed libraries, Python,
QML, icons and project licenses—not a wallet directory, model credentials, proof
logs, `node_modules`, a full Qt SDK or host font files.

Install dependency components first through Basecamp's package manager, then the
owner UI. Keep the upstream Storage and Delivery modules unmodified. A local
ad-hoc macOS signature is not notarization; LGX archive verification is not a
claim that an unsigned developer package is publisher-signed. Do not disable
operating-system or application verification globally to load a demo.

## Configure and verify the actual app

Use a distinct Core session for every headless agent, and the owner's separate
transport profile in the visible Basecamp instance. The owner's signing-key root
is supplied through `COMMONS_RELAY_OWNER_ROOT`; it is not an input supplied by the
model. Refer to `OWNER.md` for the chat, activity and manual-tool flows, and to
`SKILLS.md` for the opt-in extension root.

Run the Python engine/crypto suite and the actual QML-function contract tests:

```sh
python3 -m unittest discover -s tests -v
npm ci --ignore-scripts --prefix adapters/pi
npm --prefix adapters/pi test
node --test tests/ui-owner-form.test.mjs
```

These deterministic tests are necessary but do not prove network success. Open
the installed module in a visible Basecamp window, select an agent, wait for an
authenticated reply, and exercise the desired flow. Record the resulting task ID,
state and verified result. Loading the UI or seeing a spinning progress label is
not evidence that a transaction, model reply or private proof completed.
