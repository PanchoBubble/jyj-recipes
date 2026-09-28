import { CookingPot } from 'lucide-react'
import { useState } from 'react'

import { cn } from '@/lib/utils'

/**
 * Callers fix the box size (aspect-video or size-16) so nothing shifts when the image
 * arrives; width/height are hints for the browser's intrinsic ratio before layout.
 */
export function RecipePhoto({
  src,
  alt,
  className,
  width,
  height,
}: {
  src: string | null | undefined
  alt: string
  className?: string
  width?: number
  height?: number
}) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null)
  if (src && src !== failedSrc) {
    return (
      <img
        src={src}
        alt={alt}
        width={width}
        height={height}
        loading="lazy"
        decoding="async"
        onError={() => setFailedSrc(src)}
        className={cn('bg-muted object-cover', className)}
      />
    )
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
