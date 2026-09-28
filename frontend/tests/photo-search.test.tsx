import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { actionLink, toolLabel } from '@/features/chat/actions'
import type { ActionOut, ChatAction, ConversationDetail } from '@/features/chat/api'
import type { PhotoCredit, Recipe } from '@/features/recipes/api'

import { renderApp } from './render'
import { API, alice, calendarBackdrop, meAs, problem, server } from './server'

const at = '2026-09-28T10:00:00Z'
const CDN = 'https://images.pexels.com/photos'

const credit: PhotoCredit = {
  provider: 'pexels',
  photographer: 'Ana Cook',
  photographer_url: 'https://www.pexels.com/@ana',
  page_url: 'https://www.pexels.com/photo/pancakes-101/',
}

function recipe(overrides: Partial<Recipe> = {}): Recipe {
  return {
    id: 7,
    name: 'Pancakes',
    description: null,
    default_servings: 2,
    ingredient_count: 0,
    photo_url: null,
    photo_thumb_url: null,
    photo_credit: null,
    created_by: 1,
    created_at: at,
    updated_at: at,
    archived_at: null,
    ingredients: [],
    ...overrides,
  }
}

const withPexelsPhoto = recipe({
  photo_url: '/media/new.webp',
  photo_thumb_url: '/media/new_thumb.webp',
  photo_credit: credit,
})

function result(id: number, photographer = 'Ana Cook') {
  return {
    id,
    alt: `Stack of pancakes ${id}`,
    width: 4000,
    height: 3000,
    photographer,
    photographer_url: 'https://www.pexels.com/@ana',
    page_url: `https://www.pexels.com/photo/pancakes-${id}/`,
    thumb_url: `${CDN}/${id}/medium.jpeg`,
    preview_url: `${CDN}/${id}/large.jpeg`,
    provider: 'pexels' as const,
    title: null,
    license: null,
    license_url: null,
  }
}

const OV_ID = 'd5f163e9-73a2-4fc8-8033-515f4380c930'
const OV_THUMB = `/api/v1/images/thumb?provider=openverse&id=${OV_ID}`

const openverseCredit: PhotoCredit = {
  provider: 'openverse',
  photographer: 'Rod Waddington',
  photographer_url: 'https://www.flickr.com/photos/rod',
  page_url: 'https://www.flickr.com/photos/rod/509',
  title: 'Pancake stack',
  license: 'CC BY-SA 2.0',
  license_url: 'https://creativecommons.org/licenses/by-sa/2.0/',
}

const withOpenversePhoto = recipe({
  photo_url: '/media/ov.webp',
  photo_thumb_url: '/media/ov_thumb.webp',
  photo_credit: openverseCredit,
})

function openverseResult() {
  return {
    ...openverseCredit,
    id: OV_ID,
    alt: 'Pancake stack',
    width: 1024,
    height: 683,
    thumb_url: OV_THUMB,
    preview_url: OV_THUMB,
  }
}

function serveRecipe(r: Recipe) {
  server.use(http.get(`${API}/recipes/${r.id}`, () => HttpResponse.json(r)))
}

function serveSearch(respond: (q: string, page: number) => Response) {
  const calls: { q: string | null; page: string | null }[] = []
  server.use(
    http.get(`${API}/images/search`, ({ request }) => {
      const params = new URL(request.url).searchParams
      calls.push({ q: params.get('q'), page: params.get('page') })
      return respond(params.get('q') ?? '', Number(params.get('page')))
    }),
  )
  return calls
}

function serveUse(respond: () => Response | Promise<Response>) {
  const calls: { id: string; body: unknown; csrf: string | null }[] = []
  server.use(
    http.post(`${API}/recipes/:id/photo/from-search`, async ({ request, params }) => {
      calls.push({
        id: String(params.id),
        body: await request.json(),
        csrf: request.headers.get('X-Requested-With'),
      })
      return respond()
    }),
  )
  return calls
}

beforeEach(() => {
  server.use(meAs(alice), ...calendarBackdrop())
})

describe('photo search picker', () => {
  it('searches with the recipe name, previews with credit and uses the photo', async () => {
    serveRecipe(recipe())
    const searches = serveSearch((q, page) =>
      HttpResponse.json({
        provider: 'pexels',
        query: q,
        page,
        has_more: page === 1,
        results: page === 1 ? [result(101), result(102)] : [result(103, 'Bo Baker')],
      }),
    )
    const uses = serveUse(() => HttpResponse.json(withPexelsPhoto))
    const user = userEvent.setup()
    renderApp('/recipes/7')

    await user.click(await screen.findByRole('button', { name: 'Find a photo' }))
    const sheet = await screen.findByRole('dialog', { name: 'Find a photo' })
    expect(within(sheet).getByRole('searchbox', { name: 'Search photos' })).toHaveValue('Pancakes')

    const grid = await within(sheet).findByRole('list', { name: 'Photo results' })
    const thumbs = within(grid).getAllByRole('img')
    expect(thumbs).toHaveLength(2)
    expect(thumbs[0]).toHaveAttribute('src', `${CDN}/101/medium.jpeg`)
    expect(thumbs[0]).toHaveAttribute('loading', 'lazy')
    expect(searches[0]).toEqual({ q: 'Pancakes', page: '1' })

    await user.click(within(sheet).getByRole('button', { name: 'Load more' }))
    expect(await within(grid).findByRole('img', { name: 'Stack of pancakes 103' })).toBeInTheDocument()
    expect(searches[1]).toEqual({ q: 'Pancakes', page: '2' })

    await user.click(within(sheet).getByRole('button', { name: /Preview photo by Ana Cook: Stack of pancakes 101/ }))
    const preview = await screen.findByRole('dialog', { name: 'Preview' })
    expect(within(preview).getByRole('img', { name: 'Stack of pancakes 101' })).toHaveAttribute(
      'src',
      `${CDN}/101/large.jpeg`,
    )
    expect(within(preview).getByText(/Photo by/)).toHaveTextContent('Photo by Ana Cook on Pexels')
    expect(within(preview).getByRole('link', { name: 'Ana Cook' })).toHaveAttribute(
      'href',
      'https://www.pexels.com/@ana',
    )
    expect(within(preview).getByRole('link', { name: 'Pexels' })).toHaveAttribute(
      'href',
      'https://www.pexels.com/photo/pancakes-101/',
    )

    await user.click(within(preview).getByRole('button', { name: 'Use this photo' }))
    await waitFor(() => expect(uses).toHaveLength(1))
    expect(uses[0]).toEqual({ id: '7', body: { provider: 'pexels', photo_id: 101 }, csrf: 'jyj' })

    expect(await screen.findByText('Photo saved')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Preview' })).not.toBeInTheDocument())
    expect(await screen.findByRole('img', { name: 'Pancakes' })).toHaveAttribute('src', '/media/new.webp')
    expect(screen.getByText(/Photo by/)).toHaveTextContent('Photo by Ana Cook on Pexels')
  })

  it('shows a saving state and an error when using the photo fails', async () => {
    serveRecipe(recipe({ photo_url: '/media/old.webp', photo_thumb_url: '/media/old_thumb.webp' }))
    serveSearch((q, page) =>
      HttpResponse.json({ provider: 'pexels', query: q, page, has_more: false, results: [result(101)] }),
    )
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    serveUse(async () => {
      await gate
      return problem(502, 'Bad Gateway', 'the photo could not be downloaded')
    })
    const user = userEvent.setup()
    renderApp('/recipes/7')

    await user.click(await screen.findByRole('button', { name: 'Find a photo' }))
    const sheet = await screen.findByRole('dialog', { name: 'Find a photo' })
    await user.click(await within(sheet).findByRole('button', { name: /Preview photo by Ana Cook/ }))
    const preview = await screen.findByRole('dialog', { name: 'Preview' })
    expect(within(preview).getByText('This replaces the current photo.')).toBeInTheDocument()

    await user.click(within(preview).getByRole('button', { name: 'Use this photo' }))
    expect(await within(preview).findByRole('progressbar', { name: 'Saving photo' })).toBeInTheDocument()
    expect(within(preview).getByRole('button', { name: 'Saving…' })).toBeDisabled()
    release()

    expect(await screen.findByText("Couldn't use that photo")).toBeInTheDocument()
    expect(screen.getByText('the photo could not be downloaded')).toBeInTheDocument()
    expect(within(preview).getByRole('button', { name: 'Use this photo' })).toBeEnabled()
  })

  it('searches Openverse, shows the license and uses the photo by its id', async () => {
    serveRecipe(recipe())
    serveSearch((q, page) =>
      HttpResponse.json({ provider: 'openverse', query: q, page, has_more: false, results: [openverseResult()] }),
    )
    const uses = serveUse(() => HttpResponse.json(withOpenversePhoto))
    const user = userEvent.setup()
    renderApp('/recipes/7')

    await user.click(await screen.findByRole('button', { name: 'Find a photo' }))
    const sheet = await screen.findByRole('dialog', { name: 'Find a photo' })
    const grid = await within(sheet).findByRole('list', { name: 'Photo results' })
    expect(within(grid).getByRole('img')).toHaveAttribute('src', OV_THUMB)
    expect(within(sheet).getByText(/Photos via/)).toHaveTextContent('Photos via Openverse')

    await user.click(within(sheet).getByRole('button', { name: /Preview photo by Rod Waddington/ }))
    const preview = await screen.findByRole('dialog', { name: 'Preview' })
    expect(within(preview).getByLabelText('License: CC BY-SA 2.0')).toHaveTextContent('CC BY-SA 2.0')
    expect(within(preview).getByText('via Openverse')).toBeInTheDocument()
    expect(within(preview).getByText(/Photo:/)).toHaveTextContent('Photo: Pancake stack by Rod Waddington, CC BY-SA 2.0')
    expect(within(preview).getByRole('link', { name: 'CC BY-SA 2.0' })).toHaveAttribute(
      'href',
      'https://creativecommons.org/licenses/by-sa/2.0/',
    )

    await user.click(within(preview).getByRole('button', { name: 'Use this photo' }))
    await waitFor(() => expect(uses).toHaveLength(1))
    expect(uses[0].body).toEqual({ provider: 'openverse', photo_id: OV_ID })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Preview' })).not.toBeInTheDocument())
    expect(await screen.findByText(/Photo:/)).toHaveTextContent('Photo: Pancake stack by Rod Waddington, CC BY-SA 2.0')
  })

  it('keeps photo search available after an unavailable answer', async () => {
    serveRecipe(recipe())
    serveSearch(() => problem(503, 'Service Unavailable', 'photo search is unavailable right now'))
    const user = userEvent.setup()
    renderApp('/recipes/7')

    await user.click(await screen.findByRole('button', { name: 'Find a photo' }))
    const sheet = await screen.findByRole('dialog', { name: 'Find a photo' })
    expect(await within(sheet).findByRole('alert')).toHaveTextContent('photo search is unavailable right now')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Find a photo' })).toBeInTheDocument()
  })

  it('shows search errors', async () => {
    serveRecipe(recipe())
    serveSearch(() => problem(502, 'Bad Gateway', 'photo search failed; try again later'))
    const user = userEvent.setup()
    renderApp('/recipes/7')

    await user.click(await screen.findByRole('button', { name: 'Find a photo' }))
    const sheet = await screen.findByRole('dialog', { name: 'Find a photo' })
    expect(await within(sheet).findByRole('alert')).toHaveTextContent('photo search failed; try again later')
  })
})

describe('photo credit', () => {
  it('shows the Pexels credit under the recipe photo', async () => {
    serveRecipe(withPexelsPhoto)
    renderApp('/recipes/7')

    expect(await screen.findByRole('img', { name: 'Pancakes' })).toBeInTheDocument()
    expect(screen.getByText(/Photo by/)).toHaveTextContent('Photo by Ana Cook on Pexels')
    expect(screen.getByRole('link', { name: 'Ana Cook' })).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('shows the Openverse title, creator and license under the recipe photo', async () => {
    serveRecipe(withOpenversePhoto)
    renderApp('/recipes/7')

    expect(await screen.findByRole('img', { name: 'Pancakes' })).toBeInTheDocument()
    expect(screen.getByText(/Photo:/)).toHaveTextContent('Photo: Pancake stack by Rod Waddington, CC BY-SA 2.0')
    expect(screen.getByRole('link', { name: 'Pancake stack' })).toHaveAttribute(
      'href',
      'https://www.flickr.com/photos/rod/509',
    )
    expect(screen.getByRole('link', { name: 'Rod Waddington' })).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.queryByText(/on Pexels/)).not.toBeInTheDocument()
  })

  it('shows no credit for own uploads', async () => {
    serveRecipe(recipe({ photo_url: '/media/own.webp', photo_thumb_url: '/media/own_thumb.webp' }))
    renderApp('/recipes/7')

    expect(await screen.findByRole('img', { name: 'Pancakes' })).toBeInTheDocument()
    expect(screen.queryByText(/Photo by/)).not.toBeInTheDocument()
  })
})

function photosAction(data: Record<string, unknown>): ActionOut {
  return {
    id: 51,
    message_id: 2,
    tool: 'find_recipe_photos',
    status: 'executed',
    summary: 'find recipe photos',
    arguments: { query: 'pancakes', recipe_id: 7 },
    data: {
      provider: 'pexels',
      query: 'pancakes',
      recipe_id: 7,
      recipe_name: 'Pancakes',
      has_photo: false,
      photos: [
        { id: 101, alt: 'Stack of pancakes', photographer: 'Ana Cook', thumb_url: `${CDN}/101/medium.jpeg` },
        { id: 102, alt: 'Syrup', photographer: 'Bo Baker', thumb_url: `${CDN}/102/medium.jpeg` },
        { id: 666, alt: 'Elsewhere', photographer: 'Eve', thumb_url: 'https://evil.example/x.jpeg' },
      ],
      ...data,
    },
    error: null,
    created_at: at,
    executed_at: at,
  }
}

function serveChat(actions: ActionOut[]) {
  const conversation: ConversationDetail = {
    id: 7,
    title: 'Photos',
    created_at: at,
    updated_at: at,
    messages: [
      { id: 1, role: 'user', content: 'find a photo', input: 'text', transcript_confidence: null, created_at: at },
      { id: 2, role: 'assistant', content: 'Here are some.', input: null, transcript_confidence: null, created_at: at },
    ],
    actions,
  }
  server.use(
    http.get(`${API}/chat/health`, () => HttpResponse.json({ codex: { available: true, detail: 'ready' } })),
    http.get(`${API}/chat/conversations`, () => HttpResponse.json([])),
    http.get(`${API}/chat/conversations/7`, () => HttpResponse.json(conversation)),
  )
}

describe('chat photo card', () => {
  it('shows a thumbnail strip and uses a photo for the recipe', async () => {
    serveChat([photosAction({})])
    const uses = serveUse(() => HttpResponse.json(withPexelsPhoto))
    const user = userEvent.setup()
    renderApp('/chat/7')

    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    const card = await within(panel).findByRole('article', { name: 'Found photos: For Pancakes' })
    const strip = within(card).getByRole('list', { name: 'Photo results' })
    const thumbs = within(strip).getAllByRole('img')
    expect(thumbs.map((t) => t.getAttribute('src'))).toEqual([
      `${CDN}/101/medium.jpeg`,
      `${CDN}/102/medium.jpeg`,
    ])

    await user.click(within(card).getByRole('button', { name: 'Use photo by Bo Baker' }))
    await waitFor(() => expect(uses).toHaveLength(1))
    expect(uses[0]).toEqual({ id: '7', body: { provider: 'pexels', photo_id: 102 }, csrf: 'jyj' })
    expect(await screen.findByText('Photo set for Pancakes')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Use photo by Bo Baker' })).toHaveTextContent('In use')
    expect(within(card).getByRole('link', { name: /Open recipe/ })).toHaveAttribute('href', '/recipes/7')
  })

  it('asks before replacing an existing photo', async () => {
    serveChat([photosAction({ has_photo: true })])
    const uses = serveUse(() => HttpResponse.json(withPexelsPhoto))
    const user = userEvent.setup()
    renderApp('/chat/7')

    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    const card = await within(panel).findByRole('article', { name: /Found photos/ })
    await user.click(within(card).getByRole('button', { name: 'Use photo by Ana Cook' }))
    expect(uses).toHaveLength(0)
    const replace = within(card).getByRole('button', {
      name: 'Replace the photo of Pancakes with the photo by Ana Cook',
    })
    expect(replace).toHaveTextContent('Replace?')
    await user.click(replace)
    await waitFor(() => expect(uses).toHaveLength(1))
    expect(uses[0].body).toEqual({ provider: 'pexels', photo_id: 101 })
  })

  it('shows Openverse results from the thumbnail proxy and uses them by id', async () => {
    serveChat([
      photosAction({
        provider: 'openverse',
        photos: [
          { provider: 'openverse', id: OV_ID, alt: 'Stack', photographer: 'Rod', license: 'CC BY 4.0', thumb_url: OV_THUMB },
          { provider: 'openverse', id: 'x', alt: 'Direct', photographer: 'Eve', thumb_url: 'https://live.staticflickr.com/x.jpg' },
          { provider: 'openverse', id: 'y', alt: 'Cdn', photographer: 'Mal', thumb_url: `${CDN}/1/medium.jpeg` },
        ],
      }),
    ])
    const uses = serveUse(() => HttpResponse.json(withOpenversePhoto))
    const user = userEvent.setup()
    renderApp('/chat/7')

    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    const card = await within(panel).findByRole('article', { name: /Found photos/ })
    const thumbs = within(card).getAllByRole('img')
    expect(thumbs.map((t) => t.getAttribute('src'))).toEqual([OV_THUMB])
    expect(within(card).getByText('Rod · CC BY 4.0')).toBeInTheDocument()
    expect(within(card).getByText(/Photos via/)).toHaveTextContent('Photos via Openverse')

    await user.click(within(card).getByRole('button', { name: 'Use photo by Rod' }))
    await waitFor(() => expect(uses).toHaveLength(1))
    expect(uses[0].body).toEqual({ provider: 'openverse', photo_id: OV_ID })
  })

  it('has no Use buttons when the search was not for a recipe', async () => {
    serveChat([photosAction({ recipe_id: null, recipe_name: null })])
    renderApp('/chat/7')

    const panel = await screen.findByRole('dialog', { name: 'Assistant' })
    const card = await within(panel).findByRole('article', { name: 'Found photos: “pancakes”' })
    expect(within(card).getAllByRole('img')).toHaveLength(2)
    expect(within(card).queryByRole('button', { name: /Use photo/ })).not.toBeInTheDocument()
    expect(within(card).getByText(/Ask the assistant to use one/)).toBeInTheDocument()
  })

  it('labels set_recipe_photo and links to the recipe', () => {
    const action = {
      id: 52,
      tool: 'set_recipe_photo',
      status: 'executed',
      summary: 'set recipe photo: Pancakes',
      data: { recipe_id: 7, name: 'Pancakes', photo_id: 101, photographer: 'Ana Cook' },
    } as unknown as ChatAction
    expect(toolLabel(action)).toBe('Set recipe photo')
    expect(toolLabel({ tool: 'set_recipe_photo', status: 'proposed' })).toBe('Replace recipe photo')
    expect(actionLink(action)).toEqual({ to: '/recipes/7', label: 'Open recipe' })
  })
})
