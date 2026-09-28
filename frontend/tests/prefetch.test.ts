import { loaderFor, pageLoaders } from '@/routes/prefetch'

describe('route prefetch', () => {
  it.each([
    ['/calendar', pageLoaders.calendar],
    ['/recipes', pageLoaders.recipes],
    ['/recipes/new', pageLoaders.recipeEditor],
    ['/recipes/7', pageLoaders.recipeDetail],
    ['/recipes/7/edit', pageLoaders.recipeEditor],
    ['/shopping/lists/3', pageLoaders.shoppingList],
    ['/settings', pageLoaders.settings],
  ])('maps %s to its page chunk', (path, loader) => {
    expect(loaderFor(path)).toBe(loader)
  })

  it('ignores paths without a lazy page', () => {
    expect(loaderFor('/login')).toBeUndefined()
    expect(loaderFor('/chat/12')).toBeUndefined()
    expect(loaderFor('/media/recipes/7.webp')).toBeUndefined()
  })
})
