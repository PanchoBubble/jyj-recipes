import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter } from 'react-router'

import './index.css'
import App from './App.tsx'
import { createQueryClient } from '@/lib/query'
import { initTheme } from '@/lib/theme'
import { routes } from '@/routes'

initTheme()

const router = createBrowserRouter(routes)
const queryClient = createQueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App router={router} queryClient={queryClient} />
  </StrictMode>,
)
