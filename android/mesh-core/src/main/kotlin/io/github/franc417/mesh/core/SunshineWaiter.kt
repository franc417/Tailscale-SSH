package io.github.franc417.mesh.core

import java.util.concurrent.atomic.AtomicReference

/**
 * Holds the callback of the one Sunshine pairing request that is waiting for the computer's answer.
 * An answer is delivered at most once; starting a new request ends the old one, so a stale answer can never reach a
 * newer request's callback.
 */
internal class SunshineWaiter {
    private val callback = AtomicReference<((HostMessage.SunshineResult) -> Unit)?>(null)

    /** Registers [onResult]. A request that was still waiting is told it was replaced. */
    fun start(onResult: (HostMessage.SunshineResult) -> Unit) {
        callback.getAndSet(onResult)?.invoke(HostMessage.SunshineResult(Mip.SUNSHINE_FAILED, "replaced by a newer pairing request"))
    }

    /** Gives [result] to the waiting request, if there is one. Later calls do nothing until another request starts. */
    fun deliver(result: HostMessage.SunshineResult) {
        callback.getAndSet(null)?.invoke(result)
    }
}
