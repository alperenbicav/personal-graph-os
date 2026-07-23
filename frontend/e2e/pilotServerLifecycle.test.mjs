// ST09-F07 regression coverage: real child processes (not mocks) exercising the two
// lifecycle guarantees `pilot.spec.ts` depends on — a stopped server is confirmed gone
// before the caller proceeds, and a process that ignores SIGTERM is still reliably reaped.
import { spawn } from 'node:child_process'
import { describe, expect, it } from 'vitest'
import { spawnAndAwaitReady, terminateProcess, waitForExit } from './pilotServerLifecycle.ts'

/** True once the OS confirms `pid` is gone (`ESRCH`) or, on the rare non-ESRCH error, at
 * least isn't still alive under our own signal — used instead of a raw `pid` truthiness
 * check so these tests assert real process death, not merely that cleanup ran. */
function isProcessGone(pid) {
  try {
    process.kill(pid, 0)
    return false
  } catch (error) {
    return error.code === 'ESRCH'
  }
}

function spawnNodeScript(script) {
  return spawn(process.execPath, ['-e', script], { stdio: 'ignore' })
}

describe('waitForExit', () => {
  it('resolves once a running process exits normally', async () => {
    const child = spawnNodeScript('setTimeout(() => process.exit(0), 20)')
    await expect(waitForExit(child, 2_000)).resolves.toBeUndefined()
  })

  it('resolves immediately for an already-exited process', async () => {
    const child = spawnNodeScript('process.exit(0)')
    await waitForExit(child, 2_000)
    await expect(waitForExit(child, 2_000)).resolves.toBeUndefined()
  })

  it('rejects if the process outlives the timeout', async () => {
    const child = spawnNodeScript('setInterval(() => {}, 1000)')
    try {
      await expect(waitForExit(child, 100)).rejects.toThrow(/did not exit within/i)
    } finally {
      child.kill('SIGKILL')
    }
  })
})

describe('terminateProcess', () => {
  it('stops a normal process with SIGTERM and confirms it is gone', async () => {
    const child = spawnNodeScript('setInterval(() => {}, 1000)')
    await terminateProcess(child, { termTimeoutMs: 2_000, killTimeoutMs: 1_000 })
    expect(child.exitCode !== null || child.signalCode !== null).toBe(true)
    expect(child.killed || child.signalCode !== null).toBe(true)
  })

  it('escalates to SIGKILL when the process ignores SIGTERM', async () => {
    const child = spawnNodeScript(
      "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000)",
    )
    // Give the child a moment to install its SIGTERM handler before it's signaled — sending
    // SIGTERM before that registration would trigger Node's default (immediate-exit)
    // behavior instead of exercising the ignore-and-escalate path this test targets.
    await new Promise((resolve) => setTimeout(resolve, 100))
    await terminateProcess(child, { termTimeoutMs: 150, killTimeoutMs: 2_000 })
    expect(child.signalCode).toBe('SIGKILL')
  })

  it('is a no-op for an already-exited process', async () => {
    const child = spawnNodeScript('process.exit(0)')
    await waitForExit(child, 2_000)
    await expect(terminateProcess(child, { termTimeoutMs: 500 })).resolves.toBeUndefined()
  })

  it('fails closed when exit is never observed after SIGKILL, so a caller cannot proceed', async () => {
    // ST09-F07 round-4 regression: wraps a real, live process (that ignores SIGTERM, so it's
    // still alive when SIGKILL is sent — otherwise `process.kill(pid, 'SIGKILL')` would throw
    // ESRCH and short-circuit this scenario) so the signals this module sends are real OS
    // signals, but overrides the exit-notification methods to never fire — deterministically
    // reproducing "SIGKILL was sent but this code never observed the exit" without depending
    // on real OS reap timing (SIGKILL itself cannot be ignored, so that half of the scenario
    // can't be forced through a real process alone).
    const realChild = spawnNodeScript(
      "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000)",
    )
    await new Promise((resolve) => setTimeout(resolve, 100))
    const neverNotifyingChild = {
      pid: realChild.pid,
      exitCode: null,
      signalCode: null,
      once() {
        // Deliberately never invokes the 'exit' listener.
      },
      removeListener() {},
    }
    try {
      await expect(
        terminateProcess(neverNotifyingChild, { termTimeoutMs: 50, killTimeoutMs: 50 }),
      ).rejects.toThrow(/did not exit within/i)
    } finally {
      realChild.kill('SIGKILL')
    }
  })
})

describe('spawnAndAwaitReady', () => {
  it('returns the running process once awaitReady resolves', async () => {
    const child = await spawnAndAwaitReady(
      process.execPath,
      ['-e', 'setInterval(() => {}, 1000)'],
      { stdio: 'ignore' },
      async () => {},
    )
    try {
      expect(child.exitCode).toBeNull()
      expect(isProcessGone(child.pid)).toBe(false)
    } finally {
      await terminateProcess(child)
    }
  })

  it('terminates the spawned process before propagating a readiness failure', async () => {
    // ST09-F07 round-4: exercises the exact failure pilot.spec.ts's `spawnPilotServer` relies
    // on this for — a server that never becomes reachable (or a token that never appears) —
    // with a real, otherwise-long-lived process, asserting it is confirmed gone (not merely
    // that cleanup was attempted) before the rejection reaches the caller.
    let capturedPid
    await expect(
      spawnAndAwaitReady(
        process.execPath,
        ['-e', 'setInterval(() => {}, 1000)'],
        { stdio: 'ignore' },
        async (child) => {
          capturedPid = child.pid
          throw new Error('server never became ready')
        },
      ),
    ).rejects.toThrow(/server never became ready/i)

    expect(isProcessGone(capturedPid)).toBe(true)
  })
})
