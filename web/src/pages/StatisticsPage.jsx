import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

import { api } from '../api'
import Modal from '../components/Modal'
import {
  BarList, Columns, Donut, Line, Panel, SeriesTable, StackedBars, Trend, minutes, toneFor,
} from '../components/Charts'

/**
 * How the clinic is running.
 *
 * The administrator's dashboard answers "how much" — patients on record, visits this week,
 * how many are waiting now. This screen answers "how": where the time goes, who is carrying
 * what, how often the assistant was right, and who walks out before being seen.
 *
 * **Every panel prints the number of records behind it.** At this clinic's size a
 * percentage is easy to overread — "half of criticals waited over an hour" is a very
 * different statement about four patients than about four hundred. Rather than hide thin
 * data the page shows it and says how thin it is, because a chart drawn confidently over
 * six rows is the one way a statistics screen actually misleads anyone.
 *
 * Nothing here is patient-identifiable. The only person named is a member of staff, on the
 * workload panel, where seeing the distribution of work between them is the whole point.
 *
 * **Every figure is declared rather than written out.** A panel is a `figure` — a title, the
 * sample it rests on, the rows behind it, and the forms it may be drawn in. Expanding,
 * printing and switching between a ring and bars are then one implementation each rather
 * than thirteen, and a figure added later gets all three without asking.
 */
export default function StatisticsPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  // Which figure is open large, and which is being sent to the printer on its own.
  const [focused, setFocused] = useState(null)
  const [printing, setPrinting] = useState(null)

  useEffect(() => { api.statistics().then(setData).catch(setError) }, [])

  /*
   * Printing one figure.
   *
   * The whole page is hidden and a single sheet — rendered into `document.body`, outside the
   * shell being hidden — is shown in its place. Asking the browser to print a subtree is not
   * something CSS can express, so the honest way is to put exactly what should be on the
   * paper somewhere nothing else can reach it.
   *
   * The timeout is not a guess at how long React takes: `print()` blocks, so it has to be
   * handed to the browser after the sheet has been committed, and a task boundary is the
   * cheapest way to be after it.
   */
  useEffect(() => {
    if (!printing) return

    document.body.classList.add('print-single')

    const finish = () => {
      document.body.classList.remove('print-single')
      setPrinting(null)
    }

    window.addEventListener('afterprint', finish, { once: true })
    const handle = setTimeout(() => window.print(), 80)

    return () => {
      clearTimeout(handle)
      window.removeEventListener('afterprint', finish)
      document.body.classList.remove('print-single')
    }
  }, [printing])

  if (error) return <div className="page"><div className="note bad">{error.message}</div></div>
  if (!data) return <p className="empty">Counting the record…</p>

  const figures = buildFigures(data)
  const groups = [...new Set(figures.map((f) => f.group))]

  return (
    <div className="page">
      <div className="page-head">
        <p className="small muted">
          Counted from the tables as they stand, {new Date(data.generated_at).toLocaleString()}.
          Nothing here is stored, so no figure can disagree with the record it summarises.
        </p>
        <button className="primary no-print" onClick={() => window.print()}>Print all</button>
      </div>

      {groups.map((group) => (
        <div key={group}>
          <h2>{group}</h2>
          <div className="chart-grid">
            {figures.filter((f) => f.group === group).map((figure) => (
              <Panel
                key={figure.id}
                title={figure.title}
                count={figure.count}
                onExpand={() => setFocused(figure)}
                onPrint={() => setPrinting(figure)}
              >
                {figure.lede && <p className="chart-lede">{figure.lede}</p>}
                {figure.views[0].render('card')}
                {figure.foot && <p className="chart-foot">{figure.foot}</p>}
              </Panel>
            ))}
          </div>
        </div>
      ))}

      {thin(data) && (
        <p className="note">
          Several panels above rest on a few dozen records. Every figure is real — each is
          counted from the tables — but a distribution over this many rows describes the data
          that has been entered rather than the clinic itself. Read the count on each panel
          before drawing a conclusion from it.
        </p>
      )}

      {focused && (
        <Focused
          figure={focused}
          onClose={() => setFocused(null)}
          onPrint={() => setPrinting(focused)}
        />
      )}

      {printing && <PrintSheet figure={printing} generatedAt={data.generated_at} />}
    </div>
  )
}

/* ------------------------------------------------------------------ one figure, large */

/**
 * A figure opened large enough to read.
 *
 * The chart is redrawn at the bigger size rather than scaled up — a line chart stretched by
 * CSS keeps a 30-day axis crammed into the width it was laid out for, and the point of
 * opening it is to stop reading a smear.
 *
 * The table is always here, under whichever shape is chosen. It is the accessible reading of
 * every chart on this page, and it is also just the figures, which is what a reader who came
 * to copy a number down actually wanted.
 */
function Focused({ figure, onClose, onPrint }) {
  const [view, setView] = useState(figure.views[0].key)
  const chosen = figure.views.find((v) => v.key === view) ?? figure.views[0]

  return (
    <Modal title={figure.title} onClose={onClose} wide>
      <div className="focus-head">
        <div>
          <p className="muted small">{figure.count}</p>
          {figure.lede && <p className="chart-lede">{figure.lede}</p>}
        </div>

        <div className="row no-print">
          {figure.views.length > 1 && (
            <div className="seg" role="group" aria-label="How to draw this figure">
              {figure.views.map((v) => (
                <button
                  key={v.key}
                  className={v.key === view ? 'on' : ''}
                  onClick={() => setView(v.key)}
                  aria-pressed={v.key === view}
                >
                  {v.label}
                </button>
              ))}
            </div>
          )}
          <button onClick={onPrint}>Print this</button>
        </div>
      </div>

      <div className="focus-plot">{chosen.render('large')}</div>

      {figure.foot && <p className="chart-foot">{figure.foot}</p>}

      <details className="focus-table" open>
        <summary>The numbers</summary>
        <SeriesTable rows={figure.rows} value={figure.valueHeading} format={figure.format} note={figure.note} />
      </details>
    </Modal>
  )
}

/**
 * One figure, alone, on paper.
 *
 * Rendered into `document.body` so that hiding the application shell for printing cannot
 * hide this with it. Both the chart and the table go on the sheet: the shapes carry the
 * comparison, and the table survives the black-and-white printer that flattens every tone
 * to the same grey.
 */
function PrintSheet({ figure, generatedAt }) {
  return createPortal((
    <div className="print-sheet">
      <h1>{figure.title}</h1>
      <p className="print-sheet-meta">
        {figure.count} · counted {new Date(generatedAt).toLocaleString()}
      </p>

      {figure.lede && <p className="chart-lede">{figure.lede}</p>}

      {figure.views[0].render('large')}

      {figure.foot && <p className="chart-foot">{figure.foot}</p>}

      <SeriesTable rows={figure.rows} value={figure.valueHeading} format={figure.format} note={figure.note} />
    </div>
  ), document.body)
}

/* ------------------------------------------------------------------ the figures */

/**
 * Every panel on the page, as data.
 *
 * `views[0]` is the form the figure is drawn in on the page and on paper — the one chosen
 * because it fits the question. The rest are alternatives a reader may switch to once the
 * figure is open, which is a different thing from a chart type picked at random: a
 * distribution genuinely answers both "what share of the whole" and "which is biggest", and
 * which one someone needs is not knowable from here.
 *
 * A form is only offered where it is honest. Nothing offers a ring over "visits per doctor",
 * because the doctors are not parts of one thing anybody asked about.
 */
function buildFigures(data) {
  const {
    arrivals, waiting_outcomes: outcomes, triage_mix: triage, wait_by_priority: waits,
    triage_overrides: overrides, top_diagnoses: diagnoses, doctor_workload: workload,
    assistant_agreement: agreement, differential_confidence: confidence,
    investigations, age_and_sex: demographics, smoking, assistant_runs: runs,
  } = data

  const walkouts = pick(outcomes, 'left')
  const asked = agreement.n - pick(agreement, 'none_asked')
  const days = arrivals.series.map((d) => ({ label: shortDate(d.label), date: d.label, n: d.value }))

  return [
    {
      id: 'arrivals',
      group: 'The waiting room',
      title: 'Arrivals, last 30 days',
      count: count(arrivals.n, 'arrival'),
      lede: 'Every day is drawn, including the ones nobody came — a trend that skips empty '
        + 'days compresses a quiet week into a busy-looking line.',
      rows: days.map((d) => ({ label: d.label, count: d.n, key: d.date })),
      valueHeading: 'Arrivals',
      views: [
        {
          key: 'line', label: 'Line',
          render: (size) => (
            <Line points={days} series={[{ key: 'n', label: 'Arrivals' }]} height={plot(size)} />
          ),
        },
        {
          key: 'columns', label: 'Columns',
          // The original paired-column trend rather than `Columns`: thirty categories is
          // more than a category axis can label, and this one already thins it to three.
          render: () => <Trend days={days} series={[{ key: 'n', label: 'Arrivals' }]} />,
        },
      ],
    },

    {
      id: 'outcomes',
      group: 'The waiting room',
      title: 'What became of each arrival',
      count: count(outcomes.n, 'arrival'),
      lede: 'Everyone who came through the door, and how their visit ended.',
      rows: outcomes.series.map(toRow),
      views: ring(outcomes.series, 'arrivals'),
      foot: walkouts > 0
        ? `${walkouts} of ${outcomes.n} (${share(walkouts, outcomes.n)}) went home without `
          + 'being seen. It is the only outcome here that nobody chose.'
        : null,
    },

    {
      id: 'triage-mix',
      group: 'The waiting room',
      title: 'What the triage agent decided',
      count: count(triage.n, 'arrival'),
      lede: 'Its priority for each arrival — and how many it never saw, because observations '
        + 'were taken later or not at all.',
      rows: triage.series.map(toRow),
      views: ring(triage.series, 'arrivals'),
    },

    {
      id: 'waits',
      group: 'The waiting room',
      title: 'Typical wait before being seen',
      count: count(waits.n, 'patient seen', 'patients seen'),
      lede: 'The median, not the average: one patient seen the following morning drags a mean '
        + 'across the whole category until it describes nobody.',
      rows: waits.series.map((d) => ({ label: d.label, count: d.value, key: d.key, n: d.n })),
      valueHeading: 'Median wait',
      format: minutes,
      note: (row) => (row.n == null ? null : ` · ${row.n} patient${row.n === 1 ? '' : 's'}`),
      views: bars(
        waits.series.map((d) => ({ label: d.label, count: d.value, key: d.key, n: d.n })),
        {
          format: minutes,
          tone: toneFor,
          note: (row) => ` · ${row.n} patient${row.n === 1 ? '' : 's'}`,
        },
      ),
      foot: outOfOrder(waits.series)
        ? 'More urgent patients are not currently being seen sooner than less urgent ones. On '
          + 'this few records that is at least as likely to reflect when the data was entered '
          + 'as anything about the clinic.'
        : null,
    },

    {
      id: 'overrides',
      group: 'The waiting room',
      title: 'Did a nurse overrule the agent?',
      count: count(overrides.n, 'triaged arrival'),
      lede: 'The agent may raise a priority but never lower one. A nurse may do either.',
      rows: overrides.series.map(toRow),
      views: bars(overrides.series.map(toRow), { tone: toneFor }),
      foot: 'A run of upward overrides would say the rules are too lenient; a run of downward '
        + 'ones, that they are crying wolf.',
    },

    {
      id: 'diagnoses',
      group: 'Clinical work',
      title: 'What this clinic actually sees',
      count: count(diagnoses.n, 'diagnosed visit'),
      lede: 'By the diagnosis the physician chose — not by what the assistant proposed.',
      rows: diagnoses.series.map(toRow),
      // No column view: these labels are full diagnosis names, and stood on end they
      // become a row of truncated stubs.
      views: [{
        key: 'bars',
        label: 'Bars',
        render: () => (
          <BarList rows={diagnoses.series.map(toRow)} empty="No diagnosis has been recorded yet." />
        ),
      }],
    },

    {
      id: 'workload',
      group: 'Clinical work',
      title: 'Visits per doctor',
      count: count(workload.n, 'visit'),
      lede: 'Unassigned visits are shown rather than quietly dropped.',
      rows: workload.series.map(toRow),
      valueHeading: 'Visits',
      views: bars(workload.series.map(toRow)),
    },

    {
      id: 'investigations',
      group: 'Clinical work',
      title: 'Investigations ordered',
      count: count(investigations.n, 'investigation'),
      lede: 'What was asked for, and how much of it has come back.',
      rows: investigations.series.map((d) => ({
        label: d.label, count: sumParts(d), key: d.label,
      })),
      valueHeading: 'Ordered',
      views: [{
        key: 'stacked',
        label: 'Stacked',
        render: () => <StackedBars data={investigations.series} stacks={investigations.stacks} />,
      }],
    },

    {
      id: 'agreement',
      group: 'The assistant',
      title: "Was the physician's diagnosis one the assistant raised?",
      count: count(agreement.n, 'diagnosed visit'),
      lede: 'Matched on the diagnosis label, which is what a physician reads and picks from. '
        + "The assistant's ICD-10 codes are never written to the record, so comparing those "
        + 'would report perfect disagreement and mean nothing by it.',
      rows: agreement.series.map(toRow),
      views: bars(agreement.series.map(toRow), { tone: toneFor }),
      foot: asked > 0
        ? `Of ${asked} visit${asked === 1 ? '' : 's'} where a differential was requested, the `
          + `physician chose its first candidate ${pick(agreement, 'top')} time`
          + `${pick(agreement, 'top') === 1 ? '' : 's'} and a lower one ${pick(agreement, 'lower')}. `
          + '"Not on its list" is not a failure — a physician reaching a diagnosis the assistant '
          + 'had not considered is the case this design exists to leave room for.'
        : null,
    },

    {
      id: 'confidence',
      group: 'The assistant',
      title: 'How boldly it ranks what it proposes',
      count: count(confidence.n, 'candidate'),
      lede: 'Every candidate it has put on a differential, by the confidence attached.',
      rows: confidence.series.map(toRow),
      valueHeading: 'Candidates',
      views: ring(confidence.series, 'candidates'),
      foot: 'The reply format forbids percentages, so these four tiers are the entire '
        + 'vocabulary the assistant has for saying how sure it is.',
    },

    {
      id: 'runs',
      group: 'The assistant',
      title: 'Which parts of it get used',
      count: count(runs.n, 'run'),
      lede: 'One run for each request a doctor made of the assistant.',
      rows: runs.series.map(toRow),
      valueHeading: 'Runs',
      views: bars(runs.series.map(toRow), { tone: () => 'ai' }),
    },

    {
      id: 'demographics',
      group: 'Who the clinic sees',
      title: 'Age and sex',
      count: count(demographics.n, 'patient'),
      lede: 'Age from the date of birth where there is one, and the recorded age where there '
        + 'is not — both are in use on the register.',
      rows: demographics.series.map((d) => ({ label: d.label, count: sumParts(d), key: d.label })),
      valueHeading: 'Patients',
      views: [
        {
          key: 'stacked',
          label: 'Split by sex',
          render: () => <StackedBars data={demographics.series} stacks={demographics.stacks} />,
        },
        {
          key: 'columns',
          label: 'Columns',
          render: (size) => (
            <Columns
              rows={demographics.series.map((d) => ({ label: d.label, count: sumParts(d), key: d.label }))}
              height={plot(size)}
            />
          ),
        },
      ],
      foot: demographics.undated > 0
        ? `${demographics.undated} patient${demographics.undated === 1 ? ' has' : 's have'} `
          + `neither a date of birth nor a recorded age, and ${demographics.undated === 1 ? 'is' : 'are'} `
          + 'in no band above.'
        : null,
    },

    {
      id: 'smoking',
      group: 'Who the clinic sees',
      title: 'Smoking status',
      count: count(smoking.n, 'patient'),
      lede: 'The exposure that matters most in a respiratory clinic.',
      rows: smoking.series.map(toRow),
      valueHeading: 'Patients',
      views: ring(smoking.series, 'patients'),
      foot: pick(smoking, 'unknown') > 0
        ? `${pick(smoking, 'unknown')} of ${smoking.n} charts have no smoking status. It is `
          + 'asked at registration, so a gap here is a gap in the intake.'
        : null,
    },
  ]
}

/* ------------------------------------------------------------------ view builders */

/** The three ways a share-of-the-whole reads: as a ring, as bars, as columns. */
const ring = (series, unit) => [
  { key: 'donut', label: 'Ring', render: () => <Donut data={series} unit={unit} /> },
  {
    key: 'bars', label: 'Bars',
    render: () => <BarList rows={series.map(toRow)} tone={toneFor} />,
  },
  {
    key: 'columns', label: 'Columns',
    render: (size) => <Columns rows={series.map(toRow)} tone={toneFor} height={plot(size)} />,
  },
]

/** A ranking, lying down or standing up. */
const bars = (rows, opts = {}) => [
  {
    key: 'bars', label: 'Bars',
    render: () => <BarList rows={rows} {...opts} />,
  },
  {
    key: 'columns', label: 'Columns',
    render: (size) => (
      <Columns rows={rows} tone={opts.tone} format={opts.format} height={plot(size)} />
    ),
  },
]

/** Charts get more room once a figure is opened or sent to paper. */
const plot = (size) => (size === 'large' ? 300 : 190)

/* ------------------------------------------------------------------ small helpers */

/** `BarList` speaks in `{ label, count }`; the API speaks in `{ label, value }`. */
const toRow = (d) => ({ label: d.label, count: d.value, key: d.key })

const sumParts = (d) => Object.values(d.parts ?? {}).reduce((a, b) => a + b, 0)

const pick = (block, key) => block.series.find((d) => d.key === key)?.value ?? 0

const share = (part, whole) => (whole ? `${Math.round((part / whole) * 100)}%` : '—')

/**
 * "1 arrival", "37 arrivals", "24 patients seen".
 *
 * The plural is given rather than derived, because the noun is not always the last word:
 * appending an "s" turned "24 patient seen" into "24 patient seens".
 */
const count = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** "2 Sep" — enough to place a point on the axis without crowding it. */
const shortDate = (iso) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })

/**
 * Whether a more urgent band is waiting longer than a less urgent one.
 *
 * Worth saying out loud when it happens: it is the one thing this chart exists to catch,
 * and it is also exactly what small, unevenly entered data produces by accident.
 */
function outOfOrder(series) {
  const rank = { Critical: 4, Urgent: 3, Standard: 2, Low: 1 }
  const ranked = series.filter((d) => rank[d.label]).map((d) => ({ r: rank[d.label], v: d.value }))
  return ranked.some((a) => ranked.some((b) => a.r > b.r && a.v > b.v))
}

/** Whether the page as a whole stands on thin enough data to say so. */
const thin = (data) => (data.age_and_sex?.n ?? 0) < 50 || (data.arrivals?.n ?? 0) < 100
