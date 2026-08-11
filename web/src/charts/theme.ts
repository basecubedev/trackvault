import { useEffect, useState } from 'react'

const DARK = '(prefers-color-scheme: dark)'

/**
 * Whether the reader's system is asking for a dark interface.
 *
 * The stylesheet answers this on its own with a media query, and everything
 * drawn in CSS follows it without help. A chart cannot: ECharts paints onto a
 * canvas, so the colours have to be handed to it, and a palette stepped for a
 * white surface reads as smudges on a dark one.
 *
 * It subscribes rather than reading once, because the system setting changes
 * under a page that is already open -- at sunset, on most laptops.
 */
export function usePrefersDark(): boolean {
  const [dark, setDark] = useState(() => window.matchMedia(DARK).matches)

  useEffect(() => {
    const media = window.matchMedia(DARK)
    const update = () => {
      setDark(media.matches)
    }
    update()
    media.addEventListener('change', update)
    return () => {
      media.removeEventListener('change', update)
    }
  }, [])

  return dark
}
