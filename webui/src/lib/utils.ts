import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * `cn` — the shadcn/ui class-name helper: merge conditional clsx classes and
 * de-duplicate conflicting Tailwind utilities (last one wins). Used by every
 * presentational primitive so variant + override classes compose cleanly.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
