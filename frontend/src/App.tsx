import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { RouterProvider, type createBrowserRouter } from 'react-router'

import { Toaster } from '@/components/ui/sonner'

interface AppProps {
  router: ReturnType<typeof createBrowserRouter>
  queryClient: QueryClient
}

function App({ router, queryClient }: AppProps) {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster position="top-center" closeButton />
    </QueryClientProvider>
  )
}

export default App
