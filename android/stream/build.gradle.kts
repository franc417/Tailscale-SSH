plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    // Moonlight's own Java code (vendored under src/main/java/com/limelight) expects com.limelight.BuildConfig
    // and its JNI glue looks up com.limelight.nvstream.jni.MoonBridge by name, so the module keeps that namespace.
    // Mesh's own code lives in io.github.franc417.mesh.stream.
    namespace = "com.limelight"
    compileSdk = 34
    // Pinned so CI and a local build use the same compiler for the native streaming core.
    ndkVersion = "27.0.12077973"

    defaultConfig {
        minSdk = 26
        ndk {
            // 32-bit ARM is included on purpose: many entry-level phones run a 32-bit userspace.
            abiFilters += listOf("arm64-v8a", "armeabi-v7a")
        }
    }

    buildFeatures {
        buildConfig = true
    }

    externalNativeBuild {
        ndkBuild {
            // The native sources are fetched by android/third_party/fetch-moonlight-core.sh before Gradle runs.
            path = file("src/main/jni/Android.mk")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        resources {
            // BouncyCastle ships duplicate licence files
            excludes += "/META-INF/*.md"
        }
    }
}

dependencies {
    // Same BouncyCastle versions Moonlight 12.2 builds with (pairing and client certificate).
    api("org.bouncycastle:bcprov-jdk18on:1.85.2")
    api("org.bouncycastle:bcpkix-jdk18on:1.85")
    // OkHttp 4.12 on purpose: 5.x is built with a newer Kotlin than the 2.0.20 this project compiles with, and the
    // vendored NvHTTP only uses APIs that 4.12 has too.
    api("com.squareup.okhttp3:okhttp:4.12.0")
    // Rewrites the video's H.264 header so the phone's decoder does not hold back frames (same library and version Moonlight uses).
    implementation("org.jcodec:jcodec:0.2.5")

    testImplementation("junit:junit:4.13.2")
}
