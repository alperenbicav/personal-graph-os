// ST09-F03: the pure percentile/budget logic `performance-check.mjs` uses to turn raw
// per-metric samples into a pass/fail budget result, factored out so it can be unit-tested
// deterministically (`percentile.test.mjs`) independent of a real browser/server.

/**
 * Nearest-rank p95, matching `tests/acceptance/performance_fixture.py`'s `percentile_95`.
 * @param {number[]} samples
 * @returns {number}
 */
export function percentile95(samples) {
  if (samples.length === 0) throw new Error('percentile95 requires at least one sample')
  const ordered = [...samples].sort((a, b) => a - b)
  const rank = Math.ceil(0.95 * ordered.length)
  return ordered[Math.min(rank, ordered.length) - 1]
}

/**
 * @param {string} name
 * @param {number[]} samples
 * @param {number} budgetSeconds
 */
export function budgetResult(name, samples, budgetSeconds) {
  const p95Seconds = percentile95(samples)
  return {
    name,
    sample_count: samples.length,
    p95_seconds: Math.round(p95Seconds * 10000) / 10000,
    budget_seconds: budgetSeconds,
    passed: p95Seconds <= budgetSeconds,
  }
}
