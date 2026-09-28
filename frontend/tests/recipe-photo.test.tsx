import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { mealSlotKeys, plannedMealKeys } from '@/features/calendar/api'
import type { Recipe } from '@/features/recipes/api'

import { renderApp } from './render'
import { API, alice, meAs, server } from './server'

function recipe(overrides: Partial<Recipe> = {}): Recipe {
  return {
    id: 7,
    name: 'Pancakes',
    description: null,
    default_servings: 2,
    ingredient_count: 0,
    photo_url: null,
    photo_thumb_url: null,
    created_by: 1,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-01T10:00:00Z',
    archived_at: null,
    ingredients: [],
    ...overrides,
  }
}

const withPhoto = recipe({
  photo_url: '/media/recipes/7.webp',
  photo_thumb_url: '/media/recipes/7-thumb.webp',
})

/**
 * msw's XMLHttpRequest interceptor never finishes reading multipart bodies under jsdom,
 * so photo uploads (the one XHR call) go through this fake; everything else stays on msw.
 */
class FakeXhr {
  static sent: FakeXhr[] = []
  static onSend: ((xhr: FakeXhr) => void) | null = null

  method = ''
  url = ''
  withCredentials = false
  headers: Record<string, string> = {}
  body: unknown = null
  status = 0
  statusText = ''
  responseText = ''
  private responseType = ''
  upload: { onprogress: ((event: Partial<ProgressEvent>) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null

  open(method: string, url: string | URL) {
    this.method = method
    this.url = String(url)
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value
  }
  getResponseHeader(name: string) {
    return name.toLowerCase() === 'content-type' ? this.responseType : null
  }
  send(body: unknown) {
    this.body = body
    FakeXhr.sent.push(this)
    FakeXhr.onSend?.(this)
  }
  abort() {
    this.onabort?.()
  }

  get file() {
    const value = this.body instanceof FormData ? this.body.get('file') : null
    return value instanceof File ? value : null
  }
  progress(loaded: number, total: number) {
    act(() => this.upload.onprogress?.({ lengthComputable: true, loaded, total }))
  }
  reply(status: number, body: unknown, contentType = 'application/json') {
    act(() => {
      this.status = status
      this.responseType = contentType
      this.responseText = typeof body === 'string' ? body : JSON.stringify(body)
      this.onload?.()
    })
  }
}

function problemBody(status: number, title: string, detail: string) {
  return { type: 'about:blank', title, status, detail }
}

function servePhotoDelete(respond: () => Response) {
  const calls: { csrf: string | null }[] = []
  server.use(
    http.delete(`${API}/recipes/:id/photo`, ({ request }) => {
      calls.push({ csrf: request.headers.get('X-Requested-With') })
      return respond()
    }),
  )
  return calls
}

function serve(r: Recipe) {
  server.use(http.get(`${API}/recipes/${r.id}`, () => HttpResponse.json(r)))
}

const jpeg = () => new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3])], 'dinner.jpg', { type: 'image/jpeg' })

beforeAll(() => {
  URL.createObjectURL = vi.fn(() => 'blob:preview')
  URL.revokeObjectURL = vi.fn()
})

beforeEach(() => {
  FakeXhr.sent = []
  FakeXhr.onSend = null
  vi.stubGlobal('XMLHttpRequest', FakeXhr)
})

afterEach(() => vi.unstubAllGlobals())

beforeEach(() => server.use(meAs(alice)))

describe('recipe photo', () => {
  it('shows placeholders without a photo and lazy, sized thumbnails with one', async () => {
    server.use(
      http.get(`${API}/recipes`, () =>
        HttpResponse.json({
          items: [recipe({ id: 1, name: 'Soup' }), { ...withPhoto, id: 2, name: 'Stew' }],
          total: 2,
          page: 1,
          page_size: 20,
        }),
      ),
    )
    renderApp('/recipes')

    expect(await screen.findByRole('img', { name: 'Soup (no photo)' })).toBeInTheDocument()
    const thumb = screen.getByRole('img', { name: 'Stew' })
    expect(thumb).toHaveAttribute('src', '/media/recipes/7-thumb.webp')
    expect(thumb).toHaveAttribute('loading', 'lazy')
    expect(thumb).toHaveAttribute('width', '64')
    expect(thumb).toHaveAttribute('height', '64')
  })

  it('previews a picked file, then uploads it as multipart with the CSRF header', async () => {
    serve(recipe())
    const { queryClient } = renderApp('/recipes/7')
    const week = plannedMealKeys.range('2026-09-28', '2026-10-04')
    queryClient.setQueryData(week, [])
    queryClient.setQueryData(mealSlotKeys.all, [])
    const user = userEvent.setup()

    expect(await screen.findByRole('img', { name: 'Pancakes (no photo)' })).toBeInTheDocument()
    expect(screen.getByLabelText('Photo file from camera')).toHaveAttribute('capture', 'environment')
    expect(screen.getByLabelText('Photo file from library')).not.toHaveAttribute('capture')

    await user.upload(screen.getByLabelText('Photo file from library'), jpeg())
    expect(screen.getByRole('img', { name: 'Selected photo preview' })).toHaveAttribute('src', 'blob:preview')
    expect(FakeXhr.sent).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Upload photo' }))
    await waitFor(() => expect(FakeXhr.sent).toHaveLength(1))
    const [xhr] = FakeXhr.sent
    expect(xhr.method).toBe('PUT')
    expect(xhr.url).toBe(`${window.location.origin}${API}/recipes/7/photo`)
    expect(xhr.headers['X-Requested-With']).toBe('jyj')
    expect(xhr.withCredentials).toBe(true)
    expect(xhr.body).toBeInstanceOf(FormData)
    expect(xhr.file?.name).toBe('dinner.jpg')
    expect(xhr.file?.type).toBe('image/jpeg')

    xhr.progress(40, 100)
    expect(screen.getByRole('progressbar', { name: 'Photo upload' })).toHaveAttribute('aria-valuenow', '40')
    expect(screen.getByRole('button', { name: 'Uploading…' })).toBeDisabled()
    xhr.reply(200, withPhoto)

    expect(await screen.findByText('Photo saved')).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: 'Pancakes' })).toHaveAttribute('src', '/media/recipes/7.webp')
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Replace photo' })).toBeInTheDocument()
    expect(queryClient.getQueryState(week)?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(mealSlotKeys.all)?.isInvalidated).toBe(false)
  })

  it('removes the photo after confirming', async () => {
    serve(withPhoto)
    const calls = servePhotoDelete(() => HttpResponse.json(recipe()))
    const { queryClient } = renderApp('/recipes/7')
    const week = plannedMealKeys.range('2026-09-28', '2026-10-04')
    queryClient.setQueryData(week, [])
    const user = userEvent.setup()

    expect(await screen.findByRole('img', { name: 'Pancakes' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove photo' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Remove the photo?' })
    expect(calls).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))

    expect(await screen.findByText('Photo removed')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Pancakes (no photo)' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove photo' })).not.toBeInTheDocument()
    expect(calls).toEqual([{ csrf: 'jyj' }])
    expect(queryClient.getQueryState(week)?.isInvalidated).toBe(true)
  })

  it('shows the problem detail when the upload is too large and keeps the preview', async () => {
    serve(recipe())
    FakeXhr.onSend = (xhr) =>
      xhr.reply(413, problemBody(413, 'Content Too Large', 'Photo is larger than 10 MiB.'), 'application/problem+json')
    renderApp('/recipes/7')
    const user = userEvent.setup()

    await user.upload(await screen.findByLabelText('Photo file from library'), jpeg())
    await user.click(screen.getByRole('button', { name: 'Upload photo' }))

    expect(await screen.findByText("Couldn't upload the photo")).toBeInTheDocument()
    expect(screen.getByText('Photo is larger than 10 MiB.')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Selected photo preview' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload photo' })).toBeEnabled()
  })

  it('falls back to a size message when the proxy rejects without a problem body', async () => {
    serve(recipe())
    FakeXhr.onSend = (xhr) => xhr.reply(413, '<html>too big</html>', 'text/html')
    renderApp('/recipes/7')
    const user = userEvent.setup()

    await user.upload(await screen.findByLabelText('Photo file from camera'), jpeg())
    await user.click(screen.getByRole('button', { name: 'Upload photo' }))

    expect(await screen.findByText('That photo is too large. The limit is 10 MB.')).toBeInTheDocument()
  })

  it('uploads the photo after creating a new recipe', async () => {
    const created = recipe({ id: 99, name: 'Tacos' })
    const order: string[] = []
    server.use(
      http.get(`${API}/units`, () => HttpResponse.json([])),
      http.get(`${API}/ingredients`, () => HttpResponse.json([])),
      http.post(`${API}/recipes`, () => {
        order.push('create')
        return HttpResponse.json(created, { status: 201 })
      }),
    )
    serve({ ...created, photo_url: '/media/recipes/99.webp' })
    FakeXhr.onSend = (xhr) => {
      order.push(`photo ${new URL(xhr.url).pathname}`)
      xhr.reply(200, { ...created, photo_url: '/media/recipes/99.webp' })
    }
    const { router } = renderApp('/recipes/new')
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Name'), 'Tacos')
    await user.click(screen.getByRole('button', { name: 'Remove ingredient 1' }))
    await user.upload(screen.getByLabelText('Photo file from library'), jpeg())
    expect(screen.getByRole('img', { name: 'Selected photo preview' })).toBeInTheDocument()
    expect(screen.getByText('Uploads after the recipe is created.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Create recipe' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/recipes/99'))
    expect(order).toEqual(['create', `photo ${API}/recipes/99/photo`])
    expect(FakeXhr.sent[0].file?.name).toBe('dinner.jpg')
    expect(await screen.findByText('Added Tacos')).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: 'Tacos' })).toHaveAttribute('src', '/media/recipes/99.webp')
  })

  it('still lands on the new recipe when its photo upload fails', async () => {
    const created = recipe({ id: 99, name: 'Tacos' })
    server.use(
      http.get(`${API}/units`, () => HttpResponse.json([])),
      http.get(`${API}/ingredients`, () => HttpResponse.json([])),
      http.post(`${API}/recipes`, () => HttpResponse.json(created, { status: 201 })),
    )
    serve(created)
    FakeXhr.onSend = (xhr) =>
      xhr.reply(415, problemBody(415, 'Unsupported Media Type', 'Not a supported image.'), 'application/problem+json')
    const { router } = renderApp('/recipes/new')
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Name'), 'Tacos')
    await user.click(screen.getByRole('button', { name: 'Remove ingredient 1' }))
    await user.upload(screen.getByLabelText('Photo file from library'), jpeg())
    await user.click(screen.getByRole('button', { name: 'Create recipe' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/recipes/99'))
    expect(await screen.findByText("Added Tacos, but the photo didn't upload")).toBeInTheDocument()
    expect(screen.getByText(/Not a supported image\./)).toBeInTheDocument()
  })
})
