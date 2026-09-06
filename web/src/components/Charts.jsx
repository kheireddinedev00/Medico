import { useRef, useState } from 'react'

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
export function BarList({
  rows, tone = () => undefined, empty = 'Nothing recorded.',
  format = (n) => n, note,
}) {
  if (!rows || rows.length === 0) return <p className="empty small">{empty}</p>

  const largest = Math.max(...rows.map((r) => r.count), 1)

  return (
    <div className="barlist">
      {rows.map((row) => (
        <div className="barrow" key={row.label}>
          <span className="barlabel">{prettify(row.label)}</span>
          <span className="barvalue">
            {/* `format` is for rows whose count is not a count. A median wait of 1228
                is twenty hours, and nobody reads it as that unless it says so. */}
            {format(row.count)}
            {note?.(row) && <em className="barnote">{note(row)}</em>}
          </span>
          <div className="bartrack">
            {/* A non-zero row always keeps a sliver of bar: a category with one record
                drawn as nothing at all reads as a category with none. */}
            <div
              className={`barfill${tone(row) ? ` ${tone(row)}` : ''}`}
              style={{ width: `${Math.max(Math.round((row.count / largest) * 100), row.count > 0 ? 2 : 0)}%` }}
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

      {/*
        * Only the ends and the middle are labelled. Fourteen dates side by side is a smear
        * nobody reads, and thirty is worse.
        *
        * Three labels, not one blank span per day: a span per day divided the axis into
        * thirty seven-pixel cells, and each of the three dates that had something to say
        * was clipped to a single letter by the cell it sat in.
        */}
      <div className="trend-axis">
        <span>{days[0].label}</span>
        {days.length > 2 && <span>{days[Math.floor(days.length / 2)].label}</span>}
        {days.length > 1 && <span>{days[days.length - 1].label}</span>}
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

/**
 * A card with a heading and an optional count, the shape every dashboard panel uses.
 *
 * `onExpand` and `onPrint` add the two controls a statistic needs once there are enough of
 * them to crowd each other: open this one large enough to read, and take this one away on
 * paper. Both are `no-print`, because a button is not part of the figure.
 *
 * They are buttons rather than a click anywhere on the card. A card that swallows every
 * click cannot hold a link, a legend you can hover, or text you want to select.
 */
export function Panel({ title, count, children, actions, onExpand, onPrint }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2>{title}</h2>
        <div className="row">
          {actions}
          {count != null && <span className="muted small">{count}</span>}

          {onPrint && (
            <button className="icon no-print" onClick={onPrint} title={`Print "${title}"`}
                    aria-label={`Print ${title}`}>⎙</button>
          )}
          {onExpand && (
            <button className="icon no-print" onClick={onExpand} title={`Expand "${title}"`}
                    aria-label={`Expand ${title}`}>⤢</button>
          )}
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

/**
 * What each category is coloured, across every chart in the application.
 *
 * Critical is red because that is what the waiting room already paints a critical patient,
 * not because red came next in a palette. A colour meaning "urgent" on one screen and
 * "third series" on another is worse than no colour at all.
 *
 * Keyed by the value the API sends, so no chart has to translate before it can paint.
 */
const TONES = {
  CRITICAL: 'bad', URGENT: 'warn', STANDARD: 'info', LOW: 'ok', NOT_TRIAGED: 'faint',
  left: 'bad', waiting: 'warn', in_consultation: 'info', completed: 'ok',
  raised: 'warn', lowered: 'info', agreed: 'ok', untouched: 'faint',
  top: 'ai', lower: 'info', none: 'warn', none_asked: 'faint',
  current: 'bad', former: 'warn', never: 'ok', unknown: 'faint',
  'most likely': 'ai', possible: 'info', 'less likely': 'warn',
  'unlikely but must be excluded': 'faint',
  female: 'purple', male: 'info',
  resulted: 'ok', ordered: 'warn',
}

/** The queue's colours, in one place, so every chart agrees about what URGENT looks like. */
export const priorityTone = (row) => TONES[row.label]

/** The tone for a series point, by its `key` where it has one and its label otherwise. */
export const toneFor = (row) => TONES[row.key ?? row.label]

const sum = (numbers) => numbers.reduce((a, b) => a + b, 0)

/** Series with nothing behind them, so a panel can say so rather than draw an empty axis. */
export const isEmpty = (series) =>
  !series?.length || series.every((d) => (d.value ?? sum(Object.values(d.parts ?? {}))) === 0)

/**
 * A proportion, as a ring.
 *
 * The fourth shape, and the first added since the dashboards were built: a distribution
 * where the question is "what share of the whole", which bars answer badly because the
 * whole is never drawn. A ring rather than a pie because the hole holds the total, and the
 * total is the number people look for first.
 *
 * One `<circle>` per slice with `stroke-dasharray` — no trigonometry, and nothing to get
 * wrong at the seam. Every slice prints its value and its share in the legend, so the chart
 * survives a reader who cannot separate the colours, and survives the black-and-white
 * printout the administrator takes away.
 */
export function Donut({ data, unit = '', empty = 'Nothing recorded.' }) {
  if (isEmpty(data)) return <p className="empty small">{empty}</p>

  const total = sum(data.map((d) => d.value))
  const radius = 60
  const circumference = 2 * Math.PI * radius
  let offset = 0

  return (
    <div className="donut-wrap">
      <svg
        viewBox="0 0 160 160"
        className="donut"
        role="img"
        aria-label={data.filter((d) => d.value > 0).map((d) => `${d.label}: ${d.value}`).join(', ')}
      >
        <circle cx="80" cy="80" r={radius} className="donut-track" />

        {data.filter((d) => d.value > 0).map((d) => {
          const length = (d.value / total) * circumference
          const slice = (
            <circle
              key={d.key ?? d.label}
              cx="80" cy="80" r={radius}
              className={`donut-slice${toneFor(d) ? ` ${toneFor(d)}` : ''}`}
              strokeDasharray={`${length} ${circumference - length}`}
              strokeDashoffset={-offset}
            >
              <title>{`${d.label}: ${d.value} (${Math.round((d.value / total) * 100)}%)`}</title>
            </circle>
          )
          offset += length
          return slice
        })}

        <text x="80" y="76" className="donut-total">{total}</text>
        <text x="80" y="94" className="donut-unit">{unit}</text>
      </svg>

      <ul className="donut-legend">
        {data.map((d) => (
          <li key={d.key ?? d.label}>
            <i className={toneFor(d)} />
            <span className="donut-legend-label">{d.label}</span>
            <span className="donut-legend-value">
              {d.value}
              <em>{total ? ` · ${Math.round((d.value / total) * 100)}%` : ''}</em>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * One bar per category, split by a second dimension.
 *
 * For the two questions that are really "of these, how many are…": investigations that have
 * come back against those still awaited, and each age band split by sex. Two charts side by
 * side would make the reader do the division in their head.
 */
export function StackedBars({ data, stacks, empty = 'Nothing recorded.' }) {
  if (isEmpty(data)) return <p className="empty small">{empty}</p>

  const totals = data.map((d) => sum(Object.values(d.parts)))
  const largest = Math.max(...totals, 1)
  const keys = Object.keys(stacks)

  return (
    <>
      <div className="barlist">
        {data.map((d, i) => (
          <div className="barrow" key={d.label}>
            <span className="barlabel">{d.label}</span>
            <span className="barvalue">{totals[i]}</span>
            <div className="bartrack stacked">
              {/* `title` as an attribute, not a `<title>` child: outside SVG that element
                  belongs to the document head, and the browser hoists it — it renamed the
                  browser tab after the last segment drawn. */}
              {keys.map((key) => d.parts[key] > 0 && (
                <div
                  key={key}
                  className={`barfill${TONES[key] ? ` ${TONES[key]}` : ''}`}
                  style={{ width: `${(d.parts[key] / largest) * 100}%` }}
                  title={`${d.label} — ${stacks[key]}: ${d.parts[key]}`}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="legend">
        {keys.map((key) => (
          <span key={key}>
            <i className={TONES[key]} />
            {stacks[key]}
          </span>
        ))}
      </div>
    </>
  )
}

/** Minutes, said the way a person would say them. */
export function minutes(total) {
  if (total < 60) return `${total} min`

  const hours = Math.floor(total / 60)
  const rest = total % 60
  if (hours < 24) return rest ? `${hours}h ${rest}m` : `${hours}h`

  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h`
}

/* ------------------------------------------------------------------ shared plumbing */

/**
 * Axis ticks a person would have chosen: 0, 5, 10 rather than 0, 3.67, 7.33.
 *
 * The top tick sits at or above the largest value, so no mark is ever drawn past the axis
 * it is measured against.
 */
function niceTicks(max, wanted = 4) {
  const rough = max / wanted
  const magnitude = 10 ** Math.floor(Math.log10(rough || 1))

  /*
   * 1, 2, 5, 10 — and never 2.5.
   *
   * Everything charted here is a count of something countable: arrivals, patients, minutes.
   * A 2.5 step put "7.5 arrivals" and "2.5 arrivals" on the axis of a 30-day trend, which
   * are not quantities this clinic can have. The floor of 1 keeps that true at the bottom
   * of the range too, where a max of 3 would otherwise be ruled at every half.
   */
  const raw = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? magnitude * 10
  const step = Math.max(1, Math.round(raw))
  const top = Math.ceil(max / step) * step

  const ticks = []
  for (let v = 0; v <= top + step / 2; v += step) ticks.push(Math.round(v * 100) / 100)
  return ticks
}

/**
 * A trend as a line.
 *
 * Columns answer "how many that day"; a line answers "which way is this going", and over a
 * month of daily counts that is the question worth the width. Both are offered — the reader
 * switches in the expanded view — because the same series honestly answers both.
 *
 * The y-axis starts at zero and always will. A trend cropped to its own range turns a rise
 * from 9 to 11 into a doubling, and this is a chart about a clinic filling up.
 */
export function Line({
  points, series, format = (n) => n, empty = 'No activity yet.', height = 190,
}) {
  const W = 600
  const PAD = { left: 38, right: 14, top: 14, bottom: 26 }
  const plotW = W - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom

  const frame = useRef(null)
  const [at, setAt] = useState(null)

  const count = points?.length ?? 0

  const onMove = (e) => {
    const box = frame.current?.getBoundingClientRect()
    if (!box || count < 1) return

    // The SVG scales to its container, so the pointer has to be mapped back into viewBox
    // units before it can be compared with anything the chart drew.
    const px = ((e.clientX - box.left) / box.width) * W
    const i = Math.round(((px - PAD.left) / plotW) * (count - 1))
    setAt(Math.min(count - 1, Math.max(0, i)))
  }

  if (count === 0) return <p className="empty small">{empty}</p>

  const ticks = niceTicks(Math.max(1, ...points.flatMap((p) => series.map((s) => p[s.key] ?? 0))))
  const top = ticks[ticks.length - 1]

  const x = (i) => PAD.left + (count === 1 ? plotW / 2 : (i / (count - 1)) * plotW)
  const y = (v) => PAD.top + plotH - (v / top) * plotH

  const path = (key) =>
    points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p[key] ?? 0).toFixed(1)}`).join(' ')

  return (
    <div className="linechart">
      <svg
        ref={frame}
        viewBox={`0 0 ${W} ${height}`}
        className="line-svg"
        onMouseMove={onMove}
        onMouseLeave={() => setAt(null)}
        role="img"
        aria-label={`${series.map((s) => s.label).join(' and ')} across ${count} days`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="axis-y" x={PAD.left - 8} y={y(t) + 3.5}>{format(t)}</text>
          </g>
        ))}

        {series.map((s) => (
          <path
            key={s.key}
            className={`line ${s.tone ?? 'primary'}`}
            d={path(s.key)}
            vectorEffect="non-scaling-stroke"
          />
        ))}

        {/* The marker appears only where the reader is pointing. A dot on all thirty days
            is a dotted line, not a set of readable points. */}
        {at != null && (
          <g className="crosshair">
            <line
              className="rule" x1={x(at)} x2={x(at)} y1={PAD.top} y2={PAD.top + plotH}
              vectorEffect="non-scaling-stroke"
            />
            {series.map((s) => (
              <circle
                key={s.key} className={`dot ${s.tone ?? 'primary'}`}
                cx={x(at)} cy={y(points[at][s.key] ?? 0)} r="5"
              />
            ))}
          </g>
        )}

        {points.map((p, i) => (
          (i === 0 || i === count - 1 || i === Math.floor(count / 2)) && (
            <text
              key={p.label} className="axis-x" x={x(i)} y={height - 8}
              textAnchor={i === 0 ? 'start' : i === count - 1 ? 'end' : 'middle'}
            >
              {p.label}
            </text>
          )
        ))}
      </svg>

      {at != null && (
        <div
          className="chart-tip"
          style={{ left: `${(x(at) / W) * 100}%` }}
          // Past the two-thirds mark the tooltip flips to the other side of the pointer
          // rather than being clipped by the panel it lives in.
          data-flip={x(at) / W > 0.66 ? 'left' : 'right'}
        >
          <strong>{points[at].label}</strong>
          {series.map((s) => (
            <span key={s.key}>
              <i className={s.tone ?? 'primary'} />
              {s.label}: <b>{format(points[at][s.key] ?? 0)}</b>
            </span>
          ))}
        </div>
      )}

      {series.length > 1 && (
        <div className="legend">
          {series.map((s) => (
            <span key={s.key}><i className={s.tone ?? 'primary'} />{s.label}</span>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * A distribution as vertical bars.
 *
 * The same numbers `BarList` draws, stood up. Worth having because a category axis reading
 * left to right is what most people picture when they ask for a bar chart, and because
 * short labels — age bands, priorities, weekdays — are wasted on a column of rows.
 *
 * Long labels are not: `BarList` stays the better shape for "Left before being seen", and
 * the page keeps using it wherever the labels are sentences.
 */
export function Columns({
  rows, tone = () => undefined, format = (n) => n, empty = 'Nothing recorded.', height = 190,
}) {
  const [at, setAt] = useState(null)

  if (!rows || rows.length === 0) return <p className="empty small">{empty}</p>

  const ticks = niceTicks(Math.max(1, ...rows.map((r) => r.count)))
  const top = ticks[ticks.length - 1]

  return (
    <div className="columns" style={{ '--plot-h': `${height}px` }}>
      <div className="col-plot">
        <div className="col-grid" aria-hidden="true">
          {[...ticks].reverse().map((t) => (
            <div className="col-gridline" key={t}><span>{format(t)}</span></div>
          ))}
        </div>

        <div className="col-bars">
          {rows.map((row, i) => (
            <div
              className={`col-bar${at === i ? ' hot' : ''}`}
              key={row.key ?? row.label}
              onMouseEnter={() => setAt(i)}
              onMouseLeave={() => setAt(null)}
            >
              {/* The value rides above its own bar rather than hiding in a tooltip: with a
                  handful of categories there is room, and a number you can read without
                  hovering is one the printout keeps. */}
              <span className="col-value">{format(row.count)}</span>
              <div
                className={`col-fill${tone(row) ? ` ${tone(row)}` : ''}`}
                style={{ height: `${Math.max((row.count / top) * 100, row.count > 0 ? 1.5 : 0)}%` }}
                title={`${prettify(row.label)}: ${format(row.count)}`}
              />
            </div>
          ))}
        </div>
      </div>

      <div className="col-axis">
        {rows.map((row) => (
          <span key={row.key ?? row.label} title={prettify(row.label)}>{prettify(row.label)}</span>
        ))}
      </div>
    </div>
  )
}

/**
 * The numbers, as a table.
 *
 * Every panel can be read this way. It is what a screen reader gets instead of a shape, what
 * a reader who cannot separate two colours falls back to, and what someone who wanted the
 * actual figure rather than an impression of it was after in the first place.
 */
export function SeriesTable({ rows, value = 'Count', format = (n) => n, note }) {
  if (!rows || rows.length === 0) return <p className="empty small">Nothing recorded.</p>

  const total = sum(rows.map((r) => r.count ?? 0))

  return (
    <table className="series-table">
      <thead>
        <tr>
          <th scope="col">Category</th>
          <th scope="col" className="num">{value}</th>
          {total > 0 && <th scope="col" className="num">Share</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key ?? row.label}>
            <th scope="row">
              {prettify(row.label)}
              {note?.(row) && <em className="barnote">{note(row)}</em>}
            </th>
            <td className="num">{format(row.count)}</td>
            {total > 0 && <td className="num muted">{Math.round((row.count / total) * 100)}%</td>}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
