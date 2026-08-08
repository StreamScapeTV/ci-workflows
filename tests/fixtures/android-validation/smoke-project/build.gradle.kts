// Synthetic issue-owned toolchain fixture. It is never a product build.

tasks.register("verifyToolchainSmoke") {
    group = "verification"
    description = "Verify the bounded Android/JDK toolchain through real Gradle execution."

    doLast {
        val sdkRoot = System.getenv("ANDROID_SDK_ROOT")
            ?: error("ANDROID_SDK_ROOT is required")
        check(JavaVersion.current().majorVersion == "25") {
            "Gradle did not execute on JDK 25"
        }
        check(file("$sdkRoot/platforms/android-37/android.jar").isFile) {
            "Android API 37 is missing"
        }
        check(file("$sdkRoot/build-tools/37.0.0/aapt2").isFile) {
            "Android Build Tools 37 are missing"
        }
        println("Synthetic Android Gradle smoke passed.")
    }
}
