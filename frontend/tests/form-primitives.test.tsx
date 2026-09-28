import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'

// iOS Safari zooms into any focused field whose font size is under 16px.
describe('form primitives keep 16px text on mobile', () => {
  it.each([
    ['input', () => <Input aria-label="field" />, 'textbox'],
    ['textarea', () => <Textarea aria-label="field" />, 'textbox'],
    [
      'native select',
      () => (
        <NativeSelect aria-label="field">
          <NativeSelectOption value="a">A</NativeSelectOption>
        </NativeSelect>
      ),
      'combobox',
    ],
  ] as const)('%s', (_name, element, role) => {
    render(element())
    const field = screen.getByRole(role, { name: 'field' })
    expect(field).toHaveClass('text-base', 'md:text-sm')
    expect(field).not.toHaveClass('text-sm', 'text-xs')
  })
})
