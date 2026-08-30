import { useEffect, useRef, useState } from 'react'

const isPlainObject = (v) =>
  v !== null && typeof v === 'object' && !Array.isArray(v)

/**
 * Form state that survives leaving the form.
 *
 * A doctor who types findings, steps over to check an allergy and comes back should find
 * their text where they left it. Losing it is the kind of small betrayal that teaches people
 * to distrust an interface — and worse, teaches them to write clinical detail somewhere else
 * first.
 *
 * Kept in `sessionStorage`, so it also survives a reload and closes with the tab. Deliberately
 * not `localStorage`: a half-typed consultation is not something to leave on a shared clinic
 * machine after the browser closes.
 *
 * This is a convenience, never a record. Nothing here has been saved to the chart, and the
 * caller clears the draft once the real save succeeds — a draft that outlives what it drafted
 * is how two versions of the same note start disagreeing.
 */
export function useDraft(key, initial) {
  const storageKey = `cdss.draft.${key}`

  const [value, setValue] = useState(() => {
    try {
      const saved = sessionStorage.getItem(storageKey)
      if (!saved) return initial

      const parsed = JSON.parse(saved)

      // Only plain objects are merged, and then only so a field added since the draft was
      // written still gets its default rather than arriving undefined. Arrays and scalars
      // are taken whole: spreading a saved array over an object initial turns a list into
      // `{0: …, 1: …}`, which then explodes the first time something iterates it.
      const mergeable =
        isPlainObject(initial) && isPlainObject(parsed)

      return mergeable ? { ...initial, ...parsed } : parsed
    } catch {
      return initial
    }
  })

  // Skip the first write, or simply opening a form would leave a draft behind for it.
  const touched = useRef(false)

  useEffect(() => {
    if (!touched.current) {
      touched.current = true
      return
    }
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(value))
    } catch {
      // Storage full or blocked. The form still works; only the safety net is gone.
    }
  }, [storageKey, value])

  const clear = () => {
    try { sessionStorage.removeItem(storageKey) } catch { /* nothing to clean up */ }
  }

  return [value, setValue, clear]
}
