import { useEffect } from 'react'

/**
 * A dialog.
 *
 * Escape and the backdrop both close it, and focus is not trapped — this is a harness, and
 * a half-built focus trap is worse than none. If `web/` ever becomes the interface people
 * actually use, this is the first component to replace with a real one.
 *
 * `danger` marks a dialog whose confirm button does something that cannot be undone.
 */
export default function Modal({ title, children, onClose, wide = false }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="backdrop" onClick={onClose}>
      <div
        className={`modal ${wide ? 'wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}
