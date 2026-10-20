// Plain Kotlin JVM, no Android dependency, on purpose: the protocol, the certificate pinning and the
// client are the parts most likely to hide a bug, so they stay testable on an ordinary JVM. The Android
// app (screens, trackpad, keyboard) depends on this module.
plugins {
    id("org.jetbrains.kotlin.jvm")
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}

tasks.test {
    useJUnit()
    // MipIntegrationTest launches the real host agent (../../mesh_input.py) and needs python3 and openssl;
    // it skips itself where they are missing. Show test output so a skip or failure is visible in CI logs.
    testLogging {
        events("passed", "skipped", "failed")
        showStandardStreams = false
    }
}
