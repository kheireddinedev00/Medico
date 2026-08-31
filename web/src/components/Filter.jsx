import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/**
 * A dropdown whose options can be designed.
 *
 * A native `<select>` hands its list to the operating system, so the menu is a grey Windows
 * popup dropped into the middle of a glass interface and no stylesheet can reach it. This
 * is a button and a list of buttons — a real menu, in the application's own language.
 *
 * **The menu is rendered into `document.body`.** Anywhere else it gets clipped by whatever
 * it happens to be inside: a dialog's scrolling body, a card with `overflow: hidden`, a
 * table that scrolls sideways. The trigger's position on screen decides where it is drawn,
 * and it flips above the control when there is no room below.
 *
 * What it keeps from the native control, because these are the parts people rely on:
 * Escape closes, arrow keys move, Enter chooses, clicking away closes, and the trigger
 * carries the `id` a `<label htmlFor>` points at.
 *
 * What it is deliberately not used for: the ICD-10 list and anything with hundreds of
 * entries. A native select is better there — typing to jump is worth more than matching
 * colours.
 */
export default function Filter({ id, value, options, onChange, className = '' }) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [box, setBox] = useState(null)

  const trigger = useRef(null)
  const menu = useRef(null)

  /*
   * The highlighted row, mirrored where a keystroke can read it.
   *
   * Enter chooses `options[active]`, and `active` is captured when the component renders.
   * Two keystrokes inside one batch — arrow then Enter, before React has re-rendered —
   * and Enter reads the old highlight and picks the row above the one lit up. Real key
   * events arrive in separate tasks so this is hard to hit by hand, but a control that can
   * commit a value other than the one it is showing is not one to leave in a clinical form.
   */
  const activeRef = useRef(0)
  const highlight = (next) => {
    setActive((i) => {
      const to = typeof next === 'function' ? next(i) : next
      activeRef.current = to
      return to
    })
  }

  const selected = options.find((o) => o.value === value) ?? options[0]

  /** Where to draw the menu, in viewport coordinates. */
  const place = () => {
    const rect = trigger.current?.getBoundingClientRect()
    if (!rect) return

    const estimated = Math.min(options.length * 40 + 12, 280)
    const below = window.innerHeight - rect.bottom
    // Flip above when there is not room beneath and there is more room over the control.
    const flip = below < estimated && rect.top > below

    setBox({
      left: rect.left,
      width: rect.width,
      top: flip ? undefined : rect.bottom + 6,
      bottom: flip ? window.innerHeight - rect.top + 6 : undefined,
      maxHeight: Math.max(120, (flip ? rect.top : below) - 16),
    })
  }

  useLayoutEffect(() => {
    if (open) place()
  }, [open])

  useEffect(() => {
    if (!open) return

    const away = (e) => {
      if (trigger.current?.contains(e.target) || menu.current?.contains(e.target)) return
      setOpen(false)
    }

    // A menu positioned in viewport coordinates has to follow the page or close. Closing
    // is the honest option: a menu that slides away from its own control looks broken.
    const dismiss = () => setOpen(false)

    document.addEventListener('mousedown', away)
    window.addEventListener('resize', dismiss)
    // Capture, so scrolling inside a dialog closes it as well as scrolling the page.
    window.addEventListener('scroll', dismiss, true)

    return () => {
      document.removeEventListener('mousedown', away)
      window.removeEventListener('resize', dismiss)
      window.removeEventListener('scroll', dismiss, true)
    }
  }, [open])

  // Opening lands on whatever is currently chosen, not on the top of the list.
  useEffect(() => {
    if (open) highlight(Math.max(0, options.findIndex((o) => o.value === value)))
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
      highlight((i) => (i + 1) % options.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      highlight((i) => (i - 1 + options.length) % options.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      choose(options[activeRef.current])
    }
  }

  return (
    <div className={`filter${open ? ' open' : ''} ${className}`}>
      <button
        id={id}
        ref={trigger}
        type="button"
        className="filter-trigger"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {/* Titled, because a narrow cell ellipsises it — a native select truncates the
            same way, and this at least gives the full text back on hover. */}
        <span className="filter-value" title={selected?.label}>{selected?.label}</span>
        <span className="chev" aria-hidden="true">▾</span>
      </button>

      {open && box && createPortal(
        <div
          ref={menu}
          className="filter-menu"
          role="listbox"
          style={{
            position: 'fixed',
            left: box.left,
            width: box.width,
            top: box.top,
            bottom: box.bottom,
            maxHeight: box.maxHeight,
          }}
        >
          {options.map((option, i) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={`filter-option${option.value === value ? ' on' : ''}${i === active ? ' hot' : ''}`}
              // Hovering moves the keyboard cursor too, so the two never disagree about
              // which row is about to be chosen.
              onMouseEnter={() => highlight(i)}
              onClick={() => choose(option)}
            >
              <span>{option.label}</span>
              {option.value === value && <span className="tick" aria-hidden="true">✓</span>}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  )
}
