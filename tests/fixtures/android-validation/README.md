# Android validation fixtures

`cases.json` contains bounded public-request cases. `smoke-project/` is an issue-owned, non-product fixture used only to prove the mobile JDK/SDK/Gradle workflow. Its fixed launcher downloads Gradle 9.6.1 from the contract-owned official HTTPS URL, verifies the pinned SHA-256 before bounded extraction, and executes only `:verifyToolchainSmoke` inside registered disposable state. It cannot build, sign, deploy, acquire a device, or certify a product.
