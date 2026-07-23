// ST09-F07: `pilot.spec.ts`'s process lifecycle, factored out so it can be unit-tested with
// real child processes independent of a real browser/server. Two failure modes motivated
// this: (1) `stopPilotServer` sent SIGTERM without ever confirming the process actually
// exited before backing up/removing its workspace directory, so "stopped before backup" was
// race-dependent rather than guaranteed; (2) if server startup failed after `spawn()` (a
// crashed/never-ready process, or a token that never appears), the caller never received a
// `PilotServer` to clean up, leaking the child.
import { type ChildProcess, type SpawnOptions, spawn } from 'node:child_process'

/** Resolves once `childProcess` has exited (immediately if it already has), or rejects after
 * `timeoutMs` if it hasn't. */
export function waitForExit(childProcess: ChildProcess, timeoutMs: number): Promise<void> {
  if (childProcess.exitCode !== null || childProcess.signalCode !== null) {
    return Promise.resolve()
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      childProcess.removeListener('exit', onExit)
      reject(new Error(`process ${childProcess.pid ?? '?'} did not exit within ${timeoutMs}ms`))
    }, timeoutMs)
    function onExit() {
      clearTimeout(timer)
      resolve()
    }
    childProcess.once('exit', onExit)
  })
}

export interface TerminateProcessOptions {
  termTimeoutMs?: number
  killTimeoutMs?: number
}

/** Asynchronous stop: sends SIGTERM and awaits real exit with a bounded timeout, escalating
 * to SIGKILL if the process is still alive afterward, then awaits that exit too. ST09-F07
 * round 4: this now fails closed — if the process is still alive after SIGKILL and its exit
 * is never observed within `killTimeoutMs`, the returned promise rejects rather than
 * resolving, so a caller that awaits this before backing up/removing a workspace directory
 * cannot proceed on an unconfirmed "stopped" state. */
export async function terminateProcess(
  childProcess: ChildProcess,
  { termTimeoutMs = 10_000, killTimeoutMs = 5_000 }: TerminateProcessOptions = {},
): Promise<void> {
  if (childProcess.exitCode !== null || childProcess.signalCode !== null) return
  if (!childProcess.pid) return

  try {
    process.kill(childProcess.pid, 'SIGTERM')
  } catch {
    return // Already exited.
  }

  try {
    await waitForExit(childProcess, termTimeoutMs)
    return
  } catch {
    // SIGTERM did not take effect in time; escalate to SIGKILL below.
  }

  if (childProcess.exitCode !== null || childProcess.signalCode !== null) return
  if (!childProcess.pid) {
    throw new Error('process had no pid to escalate SIGKILL to after SIGTERM timed out')
  }
  try {
    process.kill(childProcess.pid, 'SIGKILL')
  } catch {
    return // Already exited.
  }
  // Deliberately not caught: an unobserved exit after SIGKILL is the one case this module
  // cannot make progress on, so it must fail closed rather than let the caller assume the
  // process is gone.
  await waitForExit(childProcess, killTimeoutMs)
}

/** Spawns `command`/`args`, then awaits `awaitReady(child)` (e.g. poll an HTTP endpoint and
 * a token file). ST09-F07: if `awaitReady` throws — a crashed process, a server that never
 * becomes reachable, a token that never appears — the spawned child is terminated (awaited,
 * via `terminateProcess`) before the error propagates, so a caller that never receives the
 * child back still can't leak it. */
export async function spawnAndAwaitReady(
  command: string,
  args: string[],
  spawnOptions: SpawnOptions,
  awaitReady: (childProcess: ChildProcess) => Promise<void>,
): Promise<ChildProcess> {
  const childProcess = spawn(command, args, spawnOptions)
  try {
    await awaitReady(childProcess)
    return childProcess
  } catch (error) {
    await terminateProcess(childProcess)
    throw error
  }
}
