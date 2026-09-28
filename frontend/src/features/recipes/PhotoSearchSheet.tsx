import { ArrowLeft, Check, ExternalLink, Search, X } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/drawer'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import type { Recipe } from '@/features/recipes/api'
import {
  PHOTO_QUERY_MAX,
  isNotConfigured,
  photoSearchErrorMessage,
  usePhotoSearch,
  useSetPhotoFromSearch,
  type PhotoSearchResult,
} from '@/features/recipes/photoSearch'
import { useDebouncedValue } from '@/features/recipes/useDebouncedValue'

interface PhotoSearchSheetProps {
  recipe: Recipe
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** Search Pexels for a recipe photo; thumbnails load straight from the Pexels CDN. */
export function PhotoSearchSheet({ recipe, open, onOpenChange }: PhotoSearchSheetProps) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent className="data-[vaul-drawer-direction=bottom]:h-[85svh]">
        {open && <SheetBody recipe={recipe} onDone={() => onOpenChange(false)} />}
      </DrawerContent>
    </Drawer>
  )
}

function SheetBody({ recipe, onDone }: { recipe: Recipe; onDone: () => void }) {
  const [query, setQuery] = useState(recipe.name.slice(0, PHOTO_QUERY_MAX))
  const [selected, setSelected] = useState<PhotoSearchResult | null>(null)
  const search = useDebouncedValue(query.trim(), 400)
  const results = usePhotoSearch(search)
  const use = useSetPhotoFromSearch()
  const photos = dedupe(results.data?.pages.flatMap((p) => p.results) ?? [])

  const onUse = (photo: PhotoSearchResult) =>
    use.mutate(
      { recipeId: recipe.id, photoId: photo.id },
      {
        onSuccess: () => {
          toast.success('Photo saved')
          onDone()
        },
        onError: (error) =>
          toast.error("Couldn't use that photo", { description: photoSearchErrorMessage(error) }),
      },
    )

  if (selected) {
    return (
      <Preview
        photo={selected}
        replaces={Boolean(recipe.photo_url)}
        saving={use.isPending}
        onBack={() => setSelected(null)}
        onUse={() => onUse(selected)}
      />
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col pb-[env(safe-area-inset-bottom)]">
      <DrawerHeader className="text-left group-data-[vaul-drawer-direction=bottom]/drawer-content:text-left">
        <DrawerTitle className="text-lg">Find a photo</DrawerTitle>
        <DrawerDescription>Free photos from Pexels for {recipe.name}.</DrawerDescription>
      </DrawerHeader>
      <div className="relative px-4 pb-2">
        <Search
          className="pointer-events-none absolute top-1/2 left-7 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          type="search"
          aria-label="Search photos"
          placeholder="Search photos"
          maxLength={PHOTO_QUERY_MAX}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="h-11 pr-10 pl-9 [&::-webkit-search-cancel-button]:hidden"
        />
        {query && (
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => setQuery('')}
            className="absolute top-0 right-4 inline-flex size-11 items-center justify-center text-muted-foreground"
          >
            <X className="size-4" aria-hidden />
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {!search ? (
          <p className="py-3 text-sm text-muted-foreground">Type what the photo should show.</p>
        ) : results.isPending ? (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="aspect-[4/3] w-full" />
            ))}
          </div>
        ) : results.isError ? (
          <p
            role="alert"
            className={
              isNotConfigured(results.error)
                ? 'py-3 text-sm text-muted-foreground'
                : 'py-3 text-sm text-destructive'
            }
          >
            {photoSearchErrorMessage(results.error)}
          </p>
        ) : photos.length === 0 ? (
          <p className="py-3 text-sm text-muted-foreground">No photos match “{search}”.</p>
        ) : (
          <ul aria-label="Photo results" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {photos.map((photo) => (
              <li key={photo.id}>
                <button
                  type="button"
                  aria-label={`Preview photo by ${photo.photographer}${photo.alt ? `: ${photo.alt}` : ''}`}
                  onClick={() => setSelected(photo)}
                  className="block aspect-[4/3] w-full overflow-hidden rounded-md bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                >
                  <img
                    src={photo.thumb_url}
                    alt={photo.alt}
                    loading="lazy"
                    decoding="async"
                    referrerPolicy="no-referrer"
                    className="size-full object-cover"
                  />
                </button>
              </li>
            ))}
          </ul>
        )}
        {results.hasNextPage && !results.isError && (
          <Button
            variant="ghost"
            className="mt-2 h-11 w-full"
            disabled={results.isFetchingNextPage}
            onClick={() => results.fetchNextPage()}
          >
            {results.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </Button>
        )}
        {photos.length > 0 && (
          <p className="mt-3 text-xs text-muted-foreground">
            Photos provided by{' '}
            <a
              href="https://www.pexels.com"
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2"
            >
              Pexels
            </a>
          </p>
        )}
      </div>
    </div>
  )
}

function Preview({
  photo,
  replaces,
  saving,
  onBack,
  onUse,
}: {
  photo: PhotoSearchResult
  replaces: boolean
  saving: boolean
  onBack: () => void
  onUse: () => void
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
      <DrawerHeader className="flex-row items-center gap-2 px-0 text-left group-data-[vaul-drawer-direction=bottom]/drawer-content:text-left">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-11"
          aria-label="Back to results"
          disabled={saving}
          onClick={onBack}
        >
          <ArrowLeft aria-hidden />
        </Button>
        <div className="min-w-0">
          <DrawerTitle className="text-lg">Preview</DrawerTitle>
          <DrawerDescription className="truncate">{photo.alt || 'Pexels photo'}</DrawerDescription>
        </div>
      </DrawerHeader>
      <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-muted">
        <img
          src={photo.preview_url}
          alt={photo.alt || `Photo by ${photo.photographer}`}
          decoding="async"
          referrerPolicy="no-referrer"
          className="size-full object-cover"
        />
        {saving && (
          <div className="absolute inset-x-0 bottom-0 flex flex-col gap-1 bg-background/85 px-3 py-2 backdrop-blur">
            <span className="text-xs font-medium">Saving photo…</span>
            <div
              role="progressbar"
              aria-label="Saving photo"
              className="h-1.5 overflow-hidden rounded-full bg-muted"
            >
              <div className="h-full w-1/3 animate-pulse bg-primary" />
            </div>
          </div>
        )}
      </div>
      <PhotoCredit
        photographer={photo.photographer}
        photographerUrl={photo.photographer_url}
        pageUrl={photo.page_url}
      />
      {replaces && <p className="text-sm text-muted-foreground">This replaces the current photo.</p>}
      <Button type="button" className="h-11" disabled={saving} onClick={onUse}>
        <Check aria-hidden /> {saving ? 'Saving…' : 'Use this photo'}
      </Button>
    </div>
  )
}

/** Pexels asks for "Photo by <photographer> on Pexels" with links back. */
export function PhotoCredit({
  photographer,
  photographerUrl,
  pageUrl,
  className = 'text-xs text-muted-foreground',
}: {
  photographer: string
  photographerUrl: string | null
  pageUrl: string | null
  className?: string
}) {
  const link = 'underline underline-offset-2 hover:text-foreground'
  return (
    <p className={className}>
      Photo by{' '}
      {photographerUrl ? (
        <a href={photographerUrl} target="_blank" rel="noopener noreferrer" className={link}>
          {photographer}
        </a>
      ) : (
        photographer
      )}{' '}
      on{' '}
      <a
        href={pageUrl ?? 'https://www.pexels.com'}
        target="_blank"
        rel="noopener noreferrer"
        className={`${link} inline-flex items-center gap-0.5`}
      >
        Pexels
        <ExternalLink className="size-3" aria-hidden />
      </a>
    </p>
  )
}

function dedupe(photos: PhotoSearchResult[]) {
  const seen = new Set<number>()
  return photos.filter((p) => (seen.has(p.id) ? false : (seen.add(p.id), true)))
}
