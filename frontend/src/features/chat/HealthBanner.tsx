import { TriangleAlert } from 'lucide-react'

export function HealthBanner({ message }: { message: string }) {
  return (
    <div
      role="status"
      className="flex items-start gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
    >
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
      <p>{message}</p>
    </div>
  )
}
