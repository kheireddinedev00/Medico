import { createContext, useContext, useEffect, useState } from 'react'

/**
 * Dark or light, remembered.
 *
 * Dark is the default because this is read in consulting rooms rather than in sunlight,
 * but the choice is the clinician's and it survives a reload. Kept in `localStorage` rather
 * than the account: it is a property of the screen someone is sitting at, and a doctor who
 * dims the machine in a dark room has not decided anything about their other one.
 */

const ThemeContext = createContext({ light: false, toggle: () => {} })

const KEY = 'medico.theme'

export function ThemeProvider({ children }) {
  const [light, setLight] = useState(() => {
    try {
      const saved = localStorage.getItem(KEY)
      if (saved) return saved === 'light'
      // No stored choice: follow the operating system rather than imposing one.
      return window.matchMedia?.('(prefers-color-scheme: light)').matches ?? false
    } catch {
      return false
    }
  })

  useEffect(() => {
    document.body.classList.toggle('light', light)
    try { localStorage.setItem(KEY, light ? 'light' : 'dark') } catch { /* private mode */ }
  }, [light])

  return (
    <ThemeContext.Provider value={{ light, toggle: () => setLight((v) => !v) }}>
      {children}
    </ThemeContext.Provider>
  )
}

export const useTheme = () => useContext(ThemeContext)
