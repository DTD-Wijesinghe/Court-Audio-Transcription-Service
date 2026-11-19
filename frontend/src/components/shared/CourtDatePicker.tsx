import { useState } from 'react'

interface CourtDatePickerProps {
  value: string
  onChange: (date: string) => void
  availableDates: string[]
  disabled?: boolean
}

function pad(n: number) { return String(n).padStart(2, '0') }
function toStr(y: number, m: number, d: number) {
  return `${y}-${pad(m + 1)}-${pad(d)}`
}

export default function CourtDatePicker({ value, onChange, availableDates, disabled }: CourtDatePickerProps) {
  const [open, setOpen] = useState(false)

  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const todayStr = toStr(today.getFullYear(), today.getMonth(), today.getDate())

  const selected = value ? new Date(value + 'T00:00:00') : today
  const [viewYear, setViewYear] = useState(selected.getFullYear())
  const [viewMonth, setViewMonth] = useState(selected.getMonth())

  const availableSet = new Set(availableDates)

  const firstDay = new Date(viewYear, viewMonth, 1).getDay()
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate()
  const monthLabel = new Date(viewYear, viewMonth).toLocaleString('default', { month: 'long', year: 'numeric' })

  function prevMonth() {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(y => y - 1) }
    else setViewMonth(m => m - 1)
  }
  function nextMonth() {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(y => y + 1) }
    else setViewMonth(m => m + 1)
  }
  function selectDay(day: number) {
    const str = toStr(viewYear, viewMonth, day)
    onChange(str)
    setOpen(false)
  }

  const cells: (number | null)[] = [
    ...Array(firstDay).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ]

  return (
    <div style={{ position: 'relative' }}>
      <button
        type="button"
        className="date-picker-btn"
        onClick={() => !disabled && setOpen(o => !o)}
        disabled={disabled}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
          <line x1="16" y1="2" x2="16" y2="6"/>
          <line x1="8" y1="2" x2="8" y2="6"/>
          <line x1="3" y1="10" x2="21" y2="10"/>
        </svg>
        {value || todayStr}
      </button>

      {open && (
        <div className="court-cal-dropdown">
          {/* Header */}
          <div className="court-cal-header">
            <button type="button" className="court-cal-nav" onClick={prevMonth}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="15 18 9 12 15 6"/>
              </svg>
            </button>
            <span className="court-cal-month">{monthLabel}</span>
            <button type="button" className="court-cal-nav" onClick={nextMonth}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="9 18 15 12 9 6"/>
              </svg>
            </button>
          </div>

          {/* Day labels */}
          <div className="court-cal-grid">
            {['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'].map(d => (
              <div key={d} className="court-cal-daylabel">{d}</div>
            ))}

            {cells.map((day, i) => {
              if (!day) return <div key={`e-${i}`} />
              const dateStr = toStr(viewYear, viewMonth, day)
              const isToday = dateStr === todayStr
              const isSelected = dateStr === value
              const isAvailable = availableSet.has(dateStr)

              let cls = 'court-cal-day'
              if (isSelected) cls += ' selected'
              else if (isToday) cls += ' today'
              else if (isAvailable) cls += ' available'

              return (
                <button
                  key={day}
                  type="button"
                  className={cls}
                  onClick={() => selectDay(day)}
                >
                  {day}
                </button>
              )
            })}
          </div>

          {/* Legend */}
          <div className="court-cal-legend">
            <span className="court-cal-legend-item">
              <span className="court-cal-legend-dot today-dot" /> Today
            </span>
            <span className="court-cal-legend-item">
              <span className="court-cal-legend-dot available-dot" /> Has files
            </span>
            <span className="court-cal-legend-item">
              <span className="court-cal-legend-dot selected-dot" /> Selected
            </span>
          </div>
        </div>
      )}
    </div>
  )
}