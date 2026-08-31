import { useEffect, useState } from 'react'

/**
 * How long this patient has been with the doctor, counting up live.
 *
 * Counted from `seen_at` — the moment the consultation was opened, recorded on the queue
 * entry — rather than from when this page loaded. That difference matters more than it
 * sounds: a doctor who refreshes does not reset the clock, and two people looking at the
 * same patient on two screens see the same figure. The prototype's timer starts at zero on
 * mount, which is fine for a mockup and wrong for a room.
 *
 * Minutes and seconds up to an hour, then hours and minutes. Nobody needs to be told a
 * consultation has been running for 4,517 seconds.
 */
export default function Elapsed({ since, className = 'pill info', label = 'with the doctor' }) {
  const start = since ? new Date(since).getTime() : null
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!start) return

    // A second is the right resolution for a clock somebody glances at, and cheap: one
    // interval per patient in the room, not per frame.
    const tick = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(tick)
  }, [start])

  if (!start) return null

  // Clamped at zero. A clock that has been counting through a clock change, or against a
  // server a few seconds ahead, must not show a negative number.
  const seconds = Math.max(0, Math.floor((now - start) / 1000))

  return (
    <span className={className} title={`Started at ${new Date(start).toLocaleTimeString()}`}>
      <span className="live-dot" aria-hidden="true" />
      {format(seconds)} {label}
    </span>
  )
}

function format(seconds) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60

  if (m < 60) return `${m}:${String(s).padStart(2, '0')}`

  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}
