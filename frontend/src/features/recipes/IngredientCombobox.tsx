import { Plus } from 'lucide-react'
import { useId, useMemo, useState } from 'react'

import { Input } from '@/components/ui/input'
import type { Ingredient } from '@/features/ingredients/api'
import { cn } from '@/lib/utils'

const MAX_OPTIONS = 8

type Option = { kind: 'existing'; ingredient: Ingredient } | { kind: 'create'; name: string }

interface Props {
  id: string
  inputRef?: React.Ref<HTMLInputElement>
  value: string
  ingredients: Ingredient[]
  invalid?: boolean
  describedBy?: string
  onType: (text: string) => void
  onPick: (ingredient: Ingredient) => void
  onCreate: (name: string) => void
}

export function IngredientCombobox({
  id,
  inputRef,
  value,
  ingredients,
  invalid,
  describedBy,
  onType,
  onPick,
  onCreate,
}: Props) {
  const listId = useId()
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  const options = useMemo<Option[]>(() => {
    const text = value.trim().toLowerCase()
    const matches = ingredients
      .filter((i) => !text || i.name.toLowerCase().includes(text))
      .sort((a, b) => {
        const aStarts = a.name.toLowerCase().startsWith(text)
        const bStarts = b.name.toLowerCase().startsWith(text)
        if (aStarts !== bStarts) return aStarts ? -1 : 1
        return a.name.localeCompare(b.name)
      })
      .slice(0, MAX_OPTIONS)
      .map((ingredient): Option => ({ kind: 'existing', ingredient }))
    const exact = ingredients.some((i) => i.name.toLowerCase() === text)
    return text && !exact ? [...matches, { kind: 'create', name: value.trim() }] : matches
  }, [ingredients, value])

  const choose = (option: Option) => {
    if (option.kind === 'existing') onPick(option.ingredient)
    else onCreate(option.name)
    setOpen(false)
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setOpen(true)
      setActive((i) => Math.min(i + 1, options.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (event.key === 'Enter' && open && options[active]) {
      event.preventDefault()
      choose(options[active])
    } else if (event.key === 'Escape' && open) {
      event.preventDefault()
      setOpen(false)
    }
  }

  const onBlur = () => {
    setOpen(false)
    const exact = ingredients.find((i) => i.name.toLowerCase() === value.trim().toLowerCase())
    if (exact && value !== exact.name) onPick(exact)
  }

  const expanded = open && options.length > 0
  const activeId = expanded ? `${listId}-${active}` : undefined

  return (
    <div className="relative">
      <Input
        id={id}
        ref={inputRef}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-activedescendant={activeId}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        autoComplete="off"
        placeholder="Search or add an ingredient"
        className="h-11"
        value={value}
        onChange={(e) => {
          onType(e.target.value)
          setActive(0)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
      />
      {expanded && (
        <ul
          id={listId}
          role="listbox"
          className="absolute inset-x-0 top-full z-20 mt-1 max-h-72 overflow-y-auto rounded-lg border bg-popover p-1 text-popover-foreground shadow-md"
        >
          {options.map((option, index) => (
            <li
              key={option.kind === 'existing' ? option.ingredient.id : 'create'}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              // Keep focus in the input so blur doesn't close the list before the click lands.
              onPointerDown={(e) => e.preventDefault()}
              onClick={() => choose(option)}
              className={cn(
                'flex min-h-11 cursor-pointer items-center gap-2 rounded-md px-3 text-base',
                index === active && 'bg-accent text-accent-foreground',
              )}
            >
              {option.kind === 'existing' ? (
                <>
                  <span className="min-w-0 flex-1 truncate">{option.ingredient.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {option.ingredient.default_unit}
                  </span>
                </>
              ) : (
                <>
                  <Plus className="size-4 shrink-0" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">Create “{option.name}”</span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
