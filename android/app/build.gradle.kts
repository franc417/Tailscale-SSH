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

    signingConfigs {
        getByName("debug") {
            // One fixed key for every debug build. The CI machine is new for every run, so without this each build was signed with a
            // different random key; Android then refuses to install it over the previous one, the old app has to be uninstalled first,
            // and uninstalling erases the app's data: the phone's pairing identity (its certificate) and the saved computers. With a
            // fixed key a new build installs over the old one and keeps both. This is a debug key with the standard debug passwords:
            // it is not a secret and must never be used for a release build.
            storeFile = file("mesh-debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
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
