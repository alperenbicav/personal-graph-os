import { useId } from 'react'

export interface SegmentedOption<T extends string> {
  value: T
  label: string
}

/** Accessible segmented toggle (radio-group semantics) for mode switches like Write/Preview
 * or Board/List (EP-2026-013 ST-01). */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: SegmentedOption<T>[]
  value: T
  onChange: (value: T) => void
  ariaLabel: string
}) {
  const groupId = useId()
  return (
    <div className="segmented" role="radiogroup" aria-label={ariaLabel} id={groupId}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className={
            option.value === value ? 'segmented-option segmented-active' : 'segmented-option'
          }
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
