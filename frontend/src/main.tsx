import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Self-hosted fonts (OFL) — bundled by Vite rather than pulled from the Google
// Fonts CDN, so an offline/self-hosted deploy renders correctly and the app makes
// no third-party request. Latin subsets only; weights match what the UI uses:
// Montserrat (--font-display: headings + buttons), Open Sans (--font-sans: body).
import '@fontsource/montserrat/latin-400.css'
import '@fontsource/montserrat/latin-500.css'
import '@fontsource/montserrat/latin-600.css'
import '@fontsource/open-sans/latin-400.css'
import '@fontsource/open-sans/latin-500.css'
import '@fontsource/open-sans/latin-600.css'
// 700 is the UA default for <strong>/<b>, which markdown bold in assistant replies
// renders in the body font. Montserrat needs no 700: every heading and button sets an
// explicit weight and none exceeds 600.
import '@fontsource/open-sans/latin-700.css'
import './index.css'
// Root, not App: it dispatches between the public todo surface and the CRM with BOTH
// branches lazy, which is what keeps a /todo visitor from downloading the CRM (#149).
// This is the entry module, so everything it reaches eagerly is downloaded by every
// visitor on every surface — importing App here would put the CRM shell back in front
// of all of them, /todo included.
import Root from './Root.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
