import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import type { MealSlot } from '@/features/calendar/plan'

import { renderApp } from './render'
import { API, alice, meAs, problem, server } from './server'

const initialSlots = (): MealSlot[] => [
  { id: 1, name: 'Breakfast', position: 0, active: true },
  { id: 2, name: 'Lunch', position: 1, active: true },
  { id: 3, name: 'Dinner', position: 2, active: true },
]

function mockSlotsApi({ inUse = [] as number[] } = {}) {
  let slots = initialSlots()
  const requests: { method: string; path: string; body?: unknown }[] = []
  server.use(
    meAs(alice),
    http.get(`${API}/meal-slots`, () => HttpResponse.json(slots)),
    http.post(`${API}/meal-slots`, async ({ request }) => {
      const body = (await request.json()) as { name: string }
      requests.push({ method: 'POST', path: '/meal-slots', body })
      const created = { id: 10 + slots.length, name: body.name, position: slots.length, active: true }
      slots = [...slots, created]
      return HttpResponse.json(created, { status: 201 })
    }),
    http.put(`${API}/meal-slots/order`, async ({ request }) => {
      const body = (await request.json()) as { ids: number[] }
      requests.push({ method: 'PUT', path: '/meal-slots/order', body })
      slots = body.ids.map((id, position) => ({ ...slots.find((s) => s.id === id)!, position }))
      return HttpResponse.json(slots)
    }),
    http.patch(`${API}/meal-slots/:id`, async ({ request, params }) => {
      const body = (await request.json()) as Partial<MealSlot>
      requests.push({ method: 'PATCH', path: `/meal-slots/${params.id}`, body })
      slots = slots.map((s) => (s.id === Number(params.id) ? { ...s, ...body } : s))
      return HttpResponse.json(slots.find((s) => s.id === Number(params.id)))
    }),
    http.delete(`${API}/meal-slots/:id`, ({ params }) => {
      requests.push({ method: 'DELETE', path: `/meal-slots/${params.id}` })
      if (inUse.includes(Number(params.id))) {
        return problem(409, 'Conflict', 'meal slot has planned meals and cannot be deleted; deactivate it instead')
      }
      slots = slots.filter((s) => s.id !== Number(params.id))
      return new HttpResponse(null, { status: 204 })
    }),
  )
  return requests
}

async function slotNames() {
  const list = await screen.findByRole('list', { name: 'Meal slots' })
  return within(list)
    .getAllByRole('button', { name: /^Edit / })
    .map((b) => b.getAttribute('aria-label')!.replace('Edit ', ''))
}

describe('meal slot settings', () => {
  it('lists slots in order with drag handles', async () => {
    mockSlotsApi()
    renderApp('/settings')

    expect(await slotNames()).toEqual(['Breakfast', 'Lunch', 'Dinner'])
    const handle = screen.getByRole('button', { name: 'Move Lunch' })
    expect(handle).toHaveAttribute('data-drag-handle')
    expect(handle).toHaveClass('touch-none')
    expect(screen.getByRole('button', { name: 'Move Breakfast up' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move Dinner down' })).toBeDisabled()
  })

  it('reorders with the up/down buttons and sends the full order', async () => {
    const requests = mockSlotsApi()
    const user = userEvent.setup()
    renderApp('/settings')
    await slotNames()

    await user.click(screen.getByRole('button', { name: 'Move Dinner up' }))
    expect(await slotNames()).toEqual(['Breakfast', 'Dinner', 'Lunch'])
    await waitFor(() =>
      expect(requests).toContainEqual({
        method: 'PUT',
        path: '/meal-slots/order',
        body: { ids: [1, 3, 2] },
      }),
    )

    await user.click(screen.getByRole('button', { name: 'Move Breakfast down' }))
    await waitFor(() =>
      expect(requests.filter((r) => r.method === 'PUT').at(-1)?.body).toEqual({ ids: [3, 1, 2] }),
    )
    expect(await slotNames()).toEqual(['Dinner', 'Breakfast', 'Lunch'])
  })

  it('adds a slot', async () => {
    const requests = mockSlotsApi()
    const user = userEvent.setup()
    renderApp('/settings')
    await slotNames()

    await user.type(screen.getByRole('textbox', { name: 'New meal slot name' }), '  Snack ')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    await waitFor(async () => expect(await slotNames()).toEqual(['Breakfast', 'Lunch', 'Dinner', 'Snack']))
    expect(requests).toContainEqual({ method: 'POST', path: '/meal-slots', body: { name: 'Snack' } })
    expect(screen.getByRole('textbox', { name: 'New meal slot name' })).toHaveValue('')
  })

  it('renames a slot inline', async () => {
    const requests = mockSlotsApi()
    const user = userEvent.setup()
    renderApp('/settings')
    await slotNames()

    await user.click(screen.getByRole('button', { name: 'Edit Lunch' }))
    const input = screen.getByRole('textbox', { name: 'Name for Lunch' })
    await user.clear(input)
    await user.type(input, 'Brunch{Enter}')

    expect(await slotNames()).toEqual(['Breakfast', 'Brunch', 'Dinner'])
    expect(requests).toContainEqual({ method: 'PATCH', path: '/meal-slots/2', body: { name: 'Brunch' } })
  })

  it('toggles a slot inactive and back', async () => {
    const requests = mockSlotsApi()
    const user = userEvent.setup()
    renderApp('/settings')
    await slotNames()

    await user.click(screen.getByRole('button', { name: 'Edit Dinner' }))
    await user.click(screen.getByRole('button', { name: 'Deactivate' }))
    const row = screen.getByRole('button', { name: 'Edit Dinner' })
    expect(await within(row).findByText('Inactive')).toBeInTheDocument()
    expect(requests).toContainEqual({ method: 'PATCH', path: '/meal-slots/3', body: { active: false } })

    await user.click(await screen.findByRole('button', { name: 'Activate' }))
    await waitFor(() => expect(within(row).queryByText('Inactive')).not.toBeInTheDocument())
  })

  it('deletes an unused slot after confirming', async () => {
    const requests = mockSlotsApi()
    const user = userEvent.setup()
    renderApp('/settings')
    await slotNames()

    await user.click(screen.getByRole('button', { name: 'Edit Breakfast' }))
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete Breakfast?' })
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    await waitFor(async () => expect(await slotNames()).toEqual(['Lunch', 'Dinner']))
    expect(requests).toContainEqual({ method: 'DELETE', path: '/meal-slots/1' })
  })

  it('offers to deactivate a slot that has planned meals', async () => {
    const requests = mockSlotsApi({ inUse: [2] })
    const user = userEvent.setup()
    renderApp('/settings')
    await slotNames()

    await user.click(screen.getByRole('button', { name: 'Edit Lunch' }))
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete Lunch?' })
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    const conflict = await screen.findByRole('alertdialog', { name: 'Lunch is in use' })
    expect(within(conflict).getByText(/Deactivate it instead/)).toBeInTheDocument()
    await user.click(within(conflict).getByRole('button', { name: 'Deactivate' }))

    await waitFor(() =>
      expect(requests).toContainEqual({ method: 'PATCH', path: '/meal-slots/2', body: { active: false } }),
    )
    expect(await slotNames()).toEqual(['Breakfast', 'Lunch', 'Dinner'])
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })
})
