import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Self-hosted fonts (OFL) — bundled by Vite rather than pulled from the Google
// Fonts CDN, so an offline/self-hosted deploy renders correctly and the app makes
// no third-party request. Latin subsets only; weights match what the UI uses:
// Montserrat (--font-display: headings + buttons), Open Sans (--font-sans: body).
import '@fontsource/montserrat/latin-400.css'
import '@fontsource/montserrat/latin-500.css'
import '@fontsource/montserrat/latin-600.css'
import '@fontsource/montserrat/latin-700.css'
import '@fontsource/open-sans/latin-400.css'
import '@fontsource/open-sans/latin-500.css'
import '@fontsource/open-sans/latin-600.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
