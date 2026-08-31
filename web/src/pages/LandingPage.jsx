import { Suspense, lazy } from 'react'
import { Link } from 'react-router-dom'
import '../styles/landing.css'

const DnaScene = lazy(() => import('../components/DnaScene'))

/**
 * The band that scrolls along the bottom.
 *
 * Written about this system rather than the prototype's generic list: these are things the
 * application actually does, so a visitor who reads one is not being told about a feature
 * that does not exist.
 */
const MARQUEE = [
  '🫁 Respiratory decision support',
  '🧠 Differentials with citations',
  '📄 Report reading',
  '⏱ NEWS2 triage',
  '💊 Allergy and interaction screening',
  '📝 SOAP notes from the record',
  '❐ Curated guideline library',
  '🔒 Audited, every step',
]

/**
 * The public front door.
 *
 * Built from the Medico prototype's landing page — the aurora, the gradient headline, the
 * DNA helix, the glass cards — but written about the product that actually exists. The
 * prototype described a platform "connecting patients, doctors and medical intelligence";
 * what this is, is decision support for a clinician who has already examined the patient.
 * Overclaiming on the front page of a clinical tool is not a marketing choice, it is a
 * safety one, and the disclaimer at the bottom is not decoration.
 *
 * The figures in the trust band are the real properties of the system rather than invented
 * ones. "50K+ patients connected" on a capstone is a number nobody can defend at a viva.
 */
export default function LandingPage() {
  return (
    <div className="landing">
      <div className="aurora" aria-hidden="true"><span /><span /><span /></div>

      <nav className="landing-nav">
        <div className="brand">
          <span className="brand-mark">🩺</span>
          <span>Medico</span>
        </div>

        <div className="landing-links">
          <a href="#what">What it does</a>
          <a href="#safety">Safety</a>
          <a href="#workflow">Workflow</a>
        </div>

        <Link className="btn-primary" to="/login">Sign in</Link>
      </nav>

      <header className="hero">
        <div className="hero-copy">
          <span className="eyebrow">Respiratory clinical decision support</span>

          <h1 className="gradient-text">
            A second opinion,<br />never the decision
          </h1>

          <p>
            Medico reads the chart, retrieves the guideline, and proposes a differential
            with citations you can check. The physician examines the patient, chooses the
            diagnosis, and signs the note. The assistant never writes to the record.
          </p>

          <div className="hero-actions">
            <Link className="btn-primary lg" to="/login">
              Sign in <span aria-hidden="true">→</span>
            </Link>
            <a className="btn-ghost lg" href="#what">See how it works</a>
          </div>
        </div>

        <div className="hero-visual">
          {/* Decorative and deliberately unhurried — the page is complete without it. */}
          <Suspense fallback={null}><DnaScene /></Suspense>
          <div className="hero-glow" aria-hidden="true" />
        </div>
      </header>

      <div className="divider" />

      <section className="band" id="safety">
        <div className="section-head">
          <span className="eyebrow">Built-in properties</span>
          <h2>Three things it will not do</h2>
          <p>
            Enforced in the engine rather than promised in the interface, and covered by
            tests that fail the build if any of them stops holding.
          </p>
        </div>

        <div className="band-grid">
          <div className="glass">
            <h3>It never writes to the record</h3>
            <p>
              Every suggestion is a proposal. The physician's decision is a separate act,
              stored separately, and an assessment nobody commits to leaves no trace.
            </p>
          </div>
          <div className="glass">
            <h3>It never offers a drug the patient reacts to</h3>
            <p>
              Suggestions pass an allergy and interaction screen before anyone sees them.
              What is filtered out is reported, not silently dropped.
            </p>
          </div>
          <div className="glass">
            <h3>It never invents precision</h3>
            <p>
              No percentages on a differential. A model that says "72% likely" is
              performing confidence it does not have, so the format refuses it.
            </p>
          </div>
        </div>
      </section>

      <div className="divider" />

      <section className="band" id="what">
        <div className="section-head">
          <span className="eyebrow">What it does</span>
          <h2>From the waiting room to the signed note</h2>
          <p>One workflow, shared by the nurse and the physician, with the record as the join.</p>
        </div>

        <div className="feature-grid">
          {[
            ['⏱', 'Triage on arrival', 'NEWS2 and a red-flag list score every arrival. The rules decide; the model may only raise the priority, never lower it. A nurse can override either, and both answers stay on screen.'],
            ['🧠', 'A differential with citations', 'Retrieved from GOLD, GINA, NICE and WHO guidance. Every entry names the findings it rests on and the pages it came from.'],
            ['🔬', 'Investigations and results', 'Order tests, upload the report, have it transcribed, then analyse it as a separate act — and decide whether it changes the diagnosis.'],
            ['💊', 'Screened treatment', 'Suggestions checked against the allergy list and current medications before they are shown, with the reasoning attached.'],
            ['📝', 'A SOAP note that is a projection', 'Assembled from the record rather than stored beside it, so the note and the chart cannot drift apart. Signed by the physician.'],
            ['❐', 'A reference library you control', 'Five curated guidelines, fixed. Beside them, protocols your clinic adds and can remove — cited the same way.'],
          ].map(([icon, title, body]) => (
            <div className="glass feature" key={title}>
              <span className="feature-icon" aria-hidden="true">{icon}</span>
              <h3>{title}</h3>
              <p>{body}</p>
            </div>
          ))}
        </div>
      </section>

      <div className="divider" />

      <section className="band" id="workflow">
        <div className="section-head">
          <span className="eyebrow">The record</span>
          <h2>Numbers that describe this system</h2>
          <p>Properties of the build, not marketing arithmetic.</p>
        </div>

        <div className="trust-grid">
          {[
            ['5', 'Curated guidelines', 'GOLD, GINA, NICE, and two WHO manuals'],
            ['1,174', 'Indexed passages', 'Page ranges chosen by hand'],
            ['8', 'Workflow states', 'A frozen transition table the UI cannot bypass'],
            ['616', 'Automated tests', 'Across the engine and the application'],
          ].map(([value, label, foot]) => (
            <div className="trust" key={label}>
              <strong>{value}</strong>
              <span>{label}</span>
              <em>{foot}</em>
            </div>
          ))}
        </div>
      </section>

      {/*
        The scrolling band, from the prototype.

        The list is written twice and the track slides exactly half its width, which is what
        makes the loop seamless: at the halfway point the second copy sits precisely where
        the first began, so the reset is invisible. Marked `aria-hidden` — it is a mood, and
        a screen reader reading fourteen items twice would be worse than silence.
      */}
      <div className="marquee" aria-hidden="true">
        <div className="marquee-track">
          {[...MARQUEE, ...MARQUEE].map((item, i) => (
            <span key={i}>{item}</span>
          ))}
        </div>
      </div>

      <footer className="landing-foot">
        <div className="brand">
          <span className="brand-mark">🩺</span>
          <span>Medico</span>
        </div>

        <p>
          Decision support for qualified clinicians. Not a medical device. The physician is
          responsible for every clinical decision.
        </p>

        <Link className="btn-ghost" to="/login">Sign in</Link>
      </footer>
    </div>
  )
}
