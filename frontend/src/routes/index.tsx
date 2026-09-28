import { Navigate, type RouteObject } from 'react-router'

import { AppLayout, type RouteHandle } from '@/components/layout/AppLayout'
import { LoginPage } from '@/features/auth/LoginPage'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { CalendarPage } from '@/features/calendar/CalendarPage'
import { ChatPage } from '@/features/chat/ChatPage'
import { ConversationPage } from '@/features/chat/ConversationPage'
import { RecipeDetailPage } from '@/features/recipes/RecipeDetailPage'
import { RecipeEditorPage } from '@/features/recipes/RecipeEditorPage'
import { RecipesPage } from '@/features/recipes/RecipesPage'
import { ShoppingListPage } from '@/features/shopping/ShoppingListPage'
import { ShoppingPage } from '@/features/shopping/ShoppingPage'
import { StockPage } from '@/features/stock/StockPage'
import { SettingsPage } from '@/routes/SettingsPage'

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="/calendar" replace /> },
          {
            path: 'calendar',
            element: <CalendarPage />,
            handle: { title: 'Calendar' } satisfies RouteHandle,
          },
          {
            path: 'recipes',
            handle: { title: 'Recipes' } satisfies RouteHandle,
            children: [
              { index: true, element: <RecipesPage /> },
              {
                path: 'new',
                element: <RecipeEditorPage />,
                handle: { title: 'New recipe' } satisfies RouteHandle,
              },
              {
                path: ':id',
                element: <RecipeDetailPage />,
                handle: { title: 'Recipe' } satisfies RouteHandle,
              },
              {
                path: ':id/edit',
                element: <RecipeEditorPage />,
                handle: { title: 'Edit recipe' } satisfies RouteHandle,
              },
            ],
          },
          {
            path: 'shopping',
            handle: { title: 'Shopping' } satisfies RouteHandle,
            children: [
              { index: true, element: <ShoppingPage /> },
              {
                path: 'lists/:id',
                element: <ShoppingListPage />,
                handle: { title: 'Shopping list' } satisfies RouteHandle,
              },
            ],
          },
          {
            path: 'stock',
            element: <StockPage />,
            handle: { title: 'Stock' } satisfies RouteHandle,
          },
          {
            path: 'chat',
            handle: { title: 'Chat' } satisfies RouteHandle,
            children: [
              { index: true, element: <ChatPage /> },
              { path: ':id', element: <ConversationPage /> },
            ],
          },
          {
            path: 'settings',
            element: <SettingsPage />,
            handle: { title: 'Settings' } satisfies RouteHandle,
          },
          { path: '*', element: <Navigate to="/calendar" replace /> },
        ],
      },
    ],
  },
]
