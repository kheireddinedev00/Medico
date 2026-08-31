import { useEffect, useRef, useState } from 'react'

/**
 * A number that counts up to its value.
 *
 * The prototype's easing exactly: 900ms with a cubic ease-out, `1 - (1 - t)³`, so the
 * figure sprints and then settles rather than crawling to a stop.
 *
 * Two rules it follows that a decorative counter usually does not, and both matter when
 * the number is a count of waiting patients:
 *
 * **It never shows a wrong number as a final state.** The last frame is assigned exactly,
 * not computed, so rounding can never leave "6" on screen when the answer is 7.
 *
 * **It respects `prefers-reduced-motion`.** Someone who has asked for less movement gets
 * the figure immediately — the information is the number, never the animation.
 */
export function useCountUp(target, duration = 900) {
  const end = Number(target)
  const animatable = Number.isFinite(end) && end > 0

  const [value, setValue] = useState(animatable ? 0 : target)
  const frame = useRef(0)

  const settle = useRef(0)

  useEffect(() => {
    if (!animatable) {
      setValue(target)
      return
    }

    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

    /*
     * No animation when nobody can see it.
     *
     * `requestAnimationFrame` does not run in a hidden tab, so a dashboard opened in a
     * background tab would animate no frames and sit on whatever it started at — zero.
     * Someone switching to that tab would read "0 waiting" off a stale counter, which is
     * the one number on this screen that must never be wrong.
     */
    if (reduced || document.hidden) {
      setValue(end)
      return
    }

    let started = null

    const step = (now) => {
      started ??= now

      const progress = Math.min((now - started) / duration, 1)
      const eased = 1 - (1 - progress) ** 3

      if (progress < 1) {
        setValue(Math.floor(eased * end))
        frame.current = requestAnimationFrame(step)
      } else {
        // Assigned, not computed. A counter that lands on the wrong number is worse
        // than one that never moved.
        setValue(end)
      }
    }

    frame.current = requestAnimationFrame(step)

    // The backstop. If the frames stop arriving — the tab is hidden part-way, the machine
    // stalls — the true figure is put on screen anyway, a little after the animation was
    // due to finish.
    settle.current = setTimeout(() => setValue(end), duration + 120)

    return () => {
      cancelAnimationFrame(frame.current)
      clearTimeout(settle.current)
    }
  }, [end, target, duration, animatable])

  return value
}
