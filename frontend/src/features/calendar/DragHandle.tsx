import { GripVertical } from 'lucide-react'
import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

/** The only element with touch-action: none, so the rest of the page keeps scrolling. */
export function DragHandle({ className, ...props }: ComponentProps<'button'>) {
  return (
    <button
      type="button"
      data-drag-handle=""
      className={cn(
        'inline-flex size-11 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-muted-foreground select-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none active:cursor-grabbing disabled:cursor-default disabled:opacity-40 [-webkit-touch-callout:none]',
        className,
      )}
      {...props}
    >
      <GripVertical className="size-5" aria-hidden />
    </button>
  )
}
