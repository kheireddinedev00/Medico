import { useEffect, useRef, useState } from 'react'

/**
 * A dropdown whose options can be designed.
 *
 * A native `<select>` hands its list to the operating system, so the menu is a grey Windows
 * popup dropped into the middle of a glass interface and no stylesheet can reach it. This
 * is a button and a list of buttons — a real menu, in the application's own language.
 *
 * What it keeps from the native control, because these are the parts people actually rely
 * on: Escape closes, arrow keys move, Enter chooses, clicking away closes, and the trigger
 * is labelled by the same `id` a `<label htmlFor>` points at.
 *
 * What it is deliberately not used for: the ICD-10 list and anything else with hundreds of
 * entries. A native select is better there — typing to jump is behaviour worth more than
 * matching colours.
 */
export default function Filter({ id, value, options, onChange, className = '' }) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const root = useRef(null)

  const selected = options.find((o) => o.value === value) ?? options[0]

  useEffect(() => {
    if (!open) return

    const away = (e) => { if (!root.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])

  // Opening lands on whatever is currently chosen, not on the top of the list.
  useEffect(() => {
    if (open) setActive(Math.max(0, options.findIndex((o) => o.value === value)))
  }, [open, value, options])

  const choose = (option) => {
    onChange(option.value)
    setOpen(false)
  }

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { setOpen(false); return }

    if (!open && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown')) {
      e.preventDefault()
      setOpen(true)
      return
    }

    if (!open) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => (i + 1) % options.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => (i - 1 + options.length) % options.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      choose(options[active])
    }
  }

  return (
    <div className={`filter${open ? ' open' : ''} ${className}`} ref={root}>
      <button
        id={id}
        type="button"
        className="filter-trigger"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span>{selected?.label}</span>
        <span className="chev" aria-hidden="true">▾</span>
      </button>

      {open && (
        <div className="filter-menu" role="listbox">
          {options.map((option, i) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={`filter-option${option.value === value ? ' on' : ''}`}
              // Hovering moves the keyboard cursor too, so the two never disagree about
              // which row is about to be chosen.
              onMouseEnter={() => setActive(i)}
              onClick={() => choose(option)}
              style={i === active && option.value !== value
                ? { background: 'rgba(23, 175, 162, .12)' }
                : undefined}
            >
              <span>{option.label}</span>
              {option.value === value && <span className="tick" aria-hidden="true">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
