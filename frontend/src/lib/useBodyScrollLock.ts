import { useEffect } from 'react'

/**
 * Stops the page behind an overlay from scrolling while `locked`, then restores its styles and
 * scroll position. Uses overflow rather than position:fixed on the body, which makes iOS jump
 * when the keyboard opens.
 */
export function useBodyScrollLock(locked: boolean) {
  useEffect(() => {
    if (!locked) return
    const { documentElement: html, body } = document
    const saved = { html: html.style.overflow, body: body.style.overflow }
    const { scrollX, scrollY } = window
    html.style.overflow = 'hidden'
    body.style.overflow = 'hidden'
    return () => {
      html.style.overflow = saved.html
      body.style.overflow = saved.body
      if (window.scrollX !== scrollX || window.scrollY !== scrollY) window.scrollTo(scrollX, scrollY)
    }
  }, [locked])
}
