import { useCountUp } from '../useCountUp'

/**
 * The small vocabulary the dashboards are built from.
 *
 * Three shapes, deliberately: a number, a distribution and a trend. Everything the clinic
 * needs to show is one of those, and a dashboard assembled from three well-behaved parts
 * reads as one thing rather than as a collage.
 *
 * None of them use colour as the only carrier of meaning. Every bar prints its value, every
 * severity prints its word. That keeps them readable for someone who is colour-blind and
 * keeps them intact on the administrator's black-and-white printout.
 */

/**
 * One headline number, counted up to.
 *
 * `tone` tints the edge; the label always says what it is, because a colour on its own
 * is not something everyone can read.
 */
export function Stat({ value, label, foot, tone }) {
  const shown = useCountUp(value)

  return (
    <div className={`stat${tone ? ` is-${tone}` : ''}`}>
      <div className="stat-value">{value == null ? '—' : shown}</div>
      <div className="stat-label">{label}</div>
      {foot && <div className="stat-foot">{foot}</div>}
    </div>
  )
}

/**
 * A distribution as horizontal bars.
 *
 * Scaled to the largest row rather than to the total: the question these answer is "which
 * of these is biggest", and scaling to a total makes every bar a sliver as soon as one
 * category dominates.
 */
export function BarList({ rows, tone = () => undefined, empty = 'Nothing recorded.' }) {
  if (!rows || rows.length === 0) return <p className="empty small">{empty}</p>

  const largest = Math.max(...rows.map((r) => r.count), 1)

  return (
    <div className="barlist">
      {rows.map((row) => (
        <div className="barrow" key={row.label}>
          <span className="barlabel">{prettify(row.label)}</span>
          <span className="barvalue">{row.count}</span>
          <div className="bartrack">
            <div
              className={`barfill${tone(row) ? ` ${tone(row)}` : ''}`}
              style={{ width: `${Math.round((row.count / largest) * 100)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * Two weeks of daily activity, as paired columns.
 *
 * Every day is drawn even when nothing happened — a chart that omits empty days compresses
 * a quiet week into a busy-looking line, which is the one thing a trend must not do.
 */
export function Trend({ days, series }) {
  if (!days || days.length === 0) return <p className="empty small">No activity yet.</p>

  const largest = Math.max(1, ...days.flatMap((d) => series.map((s) => d[s.key])))

  return (
    <>
      <div className="trend">
        {days.map((day) => (
          <div className="col" key={day.date} title={`${day.label}: ${series.map((s) => `${day[s.key]} ${s.label.toLowerCase()}`).join(', ')}`}>
            {series.map((s) => (
              <div
                key={s.key}
                className={`bar${s.alt ? ' alt' : ''}`}
                style={{ height: `${(day[s.key] / largest) * 100}%` }}
              />
            ))}
          </div>
        ))}
      </div>

      <div className="trend-axis">
        {/* Only the ends and the middle are labelled. Fourteen dates side by side is a
            smear nobody reads. */}
        {days.map((day, i) => (
          <span key={day.date}>
            {i === 0 || i === days.length - 1 || i === Math.floor(days.length / 2) ? day.label : ''}
          </span>
        ))}
      </div>

      <div className="legend">
        {series.map((s) => (
          <span key={s.key}>
            <i style={{ background: s.alt ? 'var(--purple)' : 'var(--primary)' }} />
            {s.label}
          </span>
        ))}
      </div>
    </>
  )
}

/** A card with a heading and an optional count, the shape every dashboard panel uses. */
export function Panel({ title, count, children, actions }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2>{title}</h2>
        <div className="row">
          {actions}
          {count != null && <span className="muted small">{count}</span>}
        </div>
      </div>
      {children}
    </div>
  )
}

/** WAITING_FOR_TESTS reads badly in a sentence; "Waiting for tests" does not. */
export function prettify(label) {
  if (typeof label !== 'string') return label
  if (label === label.toUpperCase() && /[A-Z_]/.test(label)) {
    const words = label.replace(/_/g, ' ').toLowerCase()
    return words.charAt(0).toUpperCase() + words.slice(1)
  }
  return label
}

/** The queue's colours, in one place, so every chart agrees about what URGENT looks like. */
export const priorityTone = (row) => ({
  CRITICAL: 'bad',
  URGENT: 'warn',
  STANDARD: 'info',
  LOW: 'ok',
}[row.label] ?? undefined)
