import { describe, expect, it } from 'vitest'
import { budgetResult, percentile95 } from './percentile.mjs'

describe('percentile95', () => {
  it('throws on empty input', () => {
    expect(() => percentile95([])).toThrow(/at least one sample/i)
  })

  it('returns the single sample for one input', () => {
    expect(percentile95([2.5])).toBe(2.5)
  })

  it('does not depend on input ordering', () => {
    const ascending = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    const shuffled = [7, 2, 10, 4, 1, 9, 3, 6, 8, 5]
    expect(percentile95(shuffled)).toBe(percentile95(ascending))
  })

  it('applies the nearest-rank method for a 20-sample set', () => {
    // 20 samples, values 1..20: rank = ceil(0.95 * 20) = 19th smallest value.
    const samples = Array.from({ length: 20 }, (_, i) => i + 1)
    expect(percentile95(samples)).toBe(19)
  })

  it('rounds up a fractional rank rather than truncating', () => {
    // 3 samples: rank = ceil(0.95 * 3) = ceil(2.85) = 3rd (largest) value.
    expect(percentile95([1, 2, 3])).toBe(3)
  })
})

describe('budgetResult', () => {
  it('passes when p95 is exactly at the budget boundary', () => {
    const result = budgetResult('metric', [2, 2, 2], 2)
    expect(result.p95_seconds).toBe(2)
    expect(result.passed).toBe(true)
  })

  it('fails when p95 exceeds the budget', () => {
    const result = budgetResult('metric', [1, 1, 2.5], 2)
    expect(result.p95_seconds).toBe(2.5)
    expect(result.passed).toBe(false)
  })

  it('reports the exact sample count and rounds p95 to four decimal places', () => {
    const samples = [1.123456, 1.123456]
    const result = budgetResult('metric', samples, 5)
    expect(result.sample_count).toBe(2)
    expect(result.p95_seconds).toBe(1.1235)
  })
})
