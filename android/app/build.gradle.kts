plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "io.github.franc417.mesh"
    compileSdk = 34

    defaultConfig {
        applicationId = "io.github.franc417.mesh"   // proposal; changeable until the first release
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
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
            // BouncyCastle (pulled in by :stream) ships licence/manifest files that several of its jars have in common;
            // they are metadata, not code, so leaving them out of the APK is safe.
            excludes += listOf("/META-INF/*.md", "/META-INF/versions/9/OSGI-INF/MANIFEST.MF")
        }
    }
}

dependencies {
    implementation(project(":mesh-core"))
    implementation(project(":input-ui"))
    implementation(project(":stream"))
}
