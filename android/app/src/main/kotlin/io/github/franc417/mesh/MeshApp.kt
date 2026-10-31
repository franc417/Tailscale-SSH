package io.github.franc417.mesh

import android.app.Application

/** Installs the crash reporter before any screen exists, so even a crash while a screen is opening is recorded. */
class MeshApp : Application() {
    override fun onCreate() {
        super.onCreate()
        CrashReporter.install(this)
    }
}
