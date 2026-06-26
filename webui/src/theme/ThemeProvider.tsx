import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'agentware-theme'
/** Dark-mode-by-default (the design direction: "speed as design, dark by default"). */
const DEFAULT_THEME: Theme = 'dark'

interface ThemeContextValue {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function readStoredTheme(): Theme {
  if (typeof window === 'undefined') return DEFAULT_THEME
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'dark' || stored === 'light') return stored
  } catch {
    // localStorage may be unavailable (private mode / SSR) — fall back to default.
  }
  return DEFAULT_THEME
}

/** Apply the theme class to <html> so the CSS `.dark` token overrides take effect. */
function applyThemeClass(theme: Theme): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readStoredTheme)

  useEffect(() => {
    applyThemeClass(theme)
    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // Best-effort persistence; ignore storage failures.
    }
  }, [theme])

  const setTheme = useCallback((next: Theme) => setThemeState(next), [])
  const toggleTheme = useCallback(
    () => setThemeState((prev) => (prev === 'dark' ? 'light' : 'dark')),
    [],
  )

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    throw new Error('useTheme must be used within a <ThemeProvider>')
  }
  return ctx
}
