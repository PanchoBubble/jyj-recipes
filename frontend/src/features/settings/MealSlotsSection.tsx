import { closestCenter, DndContext, type DragEndEvent } from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ChevronDown, ChevronUp, Eye, EyeOff, Plus, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { mealErrorMessage, useMealSlots } from '@/features/calendar/api'
import { useCalendarSensors } from '@/features/calendar/dnd'
import type { MealSlot } from '@/features/calendar/plan'
import { DragHandle } from '@/features/calendar/SlotCell'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

import {
  useCreateMealSlot,
  useDeleteMealSlot,
  useReorderMealSlots,
  useUpdateMealSlot,
} from './api'

const NAME_MAX = 50

export function MealSlotsSection() {
  const slots = useMealSlots()
  const reorder = useReorderMealSlots()
  const sensors = useCalendarSensors()
  const [editingId, setEditingId] = useState<number | null>(null)
  const [deleting, setDeleting] = useState<MealSlot | null>(null)
  const list = slots.data ?? []

  const move = (from: number, to: number) => {
    if (to < 0 || to >= list.length || from === to) return
    reorder.mutate(arrayMove(list, from, to))
  }

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over) return
    move(
      list.findIndex((s) => s.id === active.id),
      list.findIndex((s) => s.id === over.id),
    )
  }

  const nameOf = (id: unknown) => list.find((s) => s.id === id)?.name ?? 'slot'

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Meal slots</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {slots.isPending ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-11" />
            <Skeleton className="h-11" />
          </div>
        ) : slots.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {mealErrorMessage(slots.error)}
          </p>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={onDragEnd}
            accessibility={{
              announcements: {
                onDragStart: ({ active }) => `Picked up ${nameOf(active.id)}.`,
                onDragOver: ({ active, over }) =>
                  over ? `${nameOf(active.id)} is over ${nameOf(over.id)}.` : undefined,
                onDragEnd: ({ active, over }) =>
                  over ? `${nameOf(active.id)} was moved to ${nameOf(over.id)}'s place.` : undefined,
                onDragCancel: ({ active }) => `Moving ${nameOf(active.id)} was cancelled.`,
              },
            }}
          >
            <SortableContext items={list.map((s) => s.id)} strategy={verticalListSortingStrategy}>
              <ul aria-label="Meal slots" className="flex flex-col gap-1.5">
                {list.map((slot, index) => (
                  <SlotRow
                    key={slot.id}
                    slot={slot}
                    first={index === 0}
                    last={index === list.length - 1}
                    editing={editingId === slot.id}
                    onEdit={(open) => setEditingId(open ? slot.id : null)}
                    onMove={(delta) => move(index, index + delta)}
                    onDelete={() => setDeleting(slot)}
                  />
                ))}
              </ul>
            </SortableContext>
          </DndContext>
        )}
        {list.length === 0 && slots.isSuccess && (
          <p className="text-sm text-muted-foreground">No meal slots yet.</p>
        )}
        <AddSlotForm />
      </CardContent>
      <DeleteSlotDialog slot={deleting} onClose={() => setDeleting(null)} />
    </Card>
  )
}

interface SlotRowProps {
  slot: MealSlot
  first: boolean
  last: boolean
  editing: boolean
  onEdit: (open: boolean) => void
  onMove: (delta: -1 | 1) => void
  onDelete: () => void
}

function SlotRow({ slot, first, last, editing, onEdit, onMove, onDelete }: SlotRowProps) {
  const update = useUpdateMealSlot()
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: slot.id })

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn(
        'rounded-lg border bg-card',
        isDragging && 'relative z-10 opacity-80 shadow-lg',
        !slot.active && 'bg-muted/50',
      )}
    >
      <div className="flex items-center gap-1">
        <DragHandle
          ref={setActivatorNodeRef}
          {...attributes}
          {...listeners}
          aria-label={`Move ${slot.name}`}
        />
        <button
          type="button"
          aria-expanded={editing}
          aria-label={`Edit ${slot.name}`}
          onClick={() => onEdit(!editing)}
          className="flex min-h-11 min-w-0 flex-1 items-center gap-2 rounded-md px-1 text-left focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          <span className={cn('truncate font-medium', !slot.active && 'text-muted-foreground')}>
            {slot.name}
          </span>
          {!slot.active && <Badge variant="outline">Inactive</Badge>}
        </button>
        <Button
          variant="ghost"
          size="icon"
          className="size-11"
          aria-label={`Move ${slot.name} up`}
          disabled={first}
          onClick={() => onMove(-1)}
        >
          <ChevronUp className="size-5" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-11"
          aria-label={`Move ${slot.name} down`}
          disabled={last}
          onClick={() => onMove(1)}
        >
          <ChevronDown className="size-5" aria-hidden />
        </Button>
      </div>

      {editing && (
        <div className="flex flex-col gap-2 border-t p-2">
          <RenameForm slot={slot} onDone={() => onEdit(false)} />
          <div className="grid grid-cols-2 gap-2">
            <Button
              variant="outline"
              className="h-11"
              disabled={update.isPending}
              onClick={() => update.mutate({ slot, changes: { active: !slot.active } })}
            >
              {slot.active ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
              {slot.active ? 'Deactivate' : 'Activate'}
            </Button>
            <Button variant="ghost" className="h-11 text-destructive" onClick={onDelete}>
              <Trash2 aria-hidden /> Delete
            </Button>
          </div>
        </div>
      )}
    </li>
  )
}

function RenameForm({ slot, onDone }: { slot: MealSlot; onDone: () => void }) {
  const update = useUpdateMealSlot()
  const [name, setName] = useState(slot.name)
  const trimmed = name.trim()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!trimmed) return
    if (trimmed !== slot.name) update.mutate({ slot, changes: { name: trimmed } })
    onDone()
  }

  return (
    <form onSubmit={submit} className="flex gap-2">
      <Input
        aria-label={`Name for ${slot.name}`}
        value={name}
        maxLength={NAME_MAX}
        autoFocus
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onDone()
        }}
        className="h-11"
      />
      <Button type="submit" className="h-11" disabled={!trimmed}>
        Save
      </Button>
    </form>
  )
}

function AddSlotForm() {
  const create = useCreateMealSlot()
  const [name, setName] = useState('')
  const trimmed = name.trim()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!trimmed) return
    create.mutate(trimmed, { onSuccess: () => setName('') })
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-1">
      <div className="flex gap-2">
        <Input
          aria-label="New meal slot name"
          placeholder="New slot, e.g. Snack"
          value={name}
          maxLength={NAME_MAX}
          onChange={(e) => {
            setName(e.target.value)
            if (create.isError) create.reset()
          }}
          className="h-11"
        />
        <Button type="submit" className="h-11" disabled={!trimmed || create.isPending}>
          <Plus aria-hidden /> Add
        </Button>
      </div>
      {create.isError && (
        <p role="alert" className="text-sm text-destructive">
          {mealErrorMessage(create.error)}
        </p>
      )}
    </form>
  )
}

function DeleteSlotDialog({ slot, onClose }: { slot: MealSlot | null; onClose: () => void }) {
  const remove = useDeleteMealSlot()
  const update = useUpdateMealSlot()
  // Keep the last slot on screen while the dialog animates closed.
  const [shown, setShown] = useState(slot)
  if (slot && slot !== shown) setShown(slot)

  const inUse = remove.error instanceof ApiError && remove.error.status === 409
  const close = () => {
    remove.reset()
    onClose()
  }

  return (
    <AlertDialog open={slot !== null} onOpenChange={(open) => !open && close()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {inUse ? `${shown?.name} is in use` : `Delete ${shown?.name}?`}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {inUse
              ? 'It has planned meals, so it can’t be deleted. Deactivate it instead to hide it from new plans and keep those meals.'
              : 'It disappears from the calendar. This can’t be undone.'}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {remove.isError && !inUse && (
          <p role="alert" className="text-sm text-destructive">
            {mealErrorMessage(remove.error)}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel className="h-11">Cancel</AlertDialogCancel>
          {inUse ? (
            <Button
              className="h-11"
              disabled={!shown?.active || update.isPending}
              onClick={() => {
                if (shown) update.mutate({ slot: shown, changes: { active: false } })
                close()
              }}
            >
              <EyeOff aria-hidden /> {shown?.active ? 'Deactivate' : 'Already inactive'}
            </Button>
          ) : (
            <Button
              variant="destructive"
              className="h-11"
              disabled={remove.isPending}
              onClick={() => shown && remove.mutate(shown, { onSuccess: close })}
            >
              <Trash2 aria-hidden /> {remove.isPending ? 'Deleting…' : 'Delete'}
            </Button>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
