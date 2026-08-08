# Apple validation fixtures

`smoke-project` is a product-neutral SwiftUI application target that supports
an iOS simulator, a tvOS simulator, and unsigned macOS from one checked-in
project and shared scheme.

The fixture contains no signing identity, provisioning profile, physical-device
destination, archive/export/store command, remote dependency, consumer product
name, secret, or deployment behavior. Its only purpose is to prove exact
workflow/toolchain/simulator execution and terminal cleanup.
