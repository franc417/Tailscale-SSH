plugins {
    // Plugin versions are pinned here, once, and subprojects apply them without a version.
    // (Declaring a version in a subproject as well is a well-known source of plugin version
    // conflicts in multi-module builds.) Kotlin 2.0.20 is the compiler the code is also
    // tested with outside Gradle, so what CI builds is what was checked.
    id("org.jetbrains.kotlin.jvm") version "2.0.20" apply false
}
