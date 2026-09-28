import { CookingPot } from 'lucide-react'

import { cn } from '@/lib/utils'

export function RecipePhoto({
  src,
  alt,
  className,
}: {
  src: string | null | undefined
  alt: string
  className?: string
}) {
  if (src) {
    return <img src={src} alt={alt} loading="lazy" className={cn('bg-muted object-cover', className)} />
  }
  return (
    <div
      role="img"
      aria-label={`${alt} (no photo)`}
      className={cn('flex items-center justify-center bg-muted text-muted-foreground', className)}
    >
      <CookingPot className="size-1/3 max-h-12 max-w-12" aria-hidden />
    </div>
  )
}
