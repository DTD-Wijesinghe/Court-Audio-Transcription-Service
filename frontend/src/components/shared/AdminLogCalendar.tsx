import { useState, useRef, useEffect } from 'react'
import './AdminLogCalendar.css'

interface ActiveDate {
  date: string
  severity: string
}

interface Props {
  selectedDate: string | null
  onDateChange: (date: string | null) => void
  activeDates: ActiveDate[]
  showLegend?: boolean
}

const MONTHS = ['January','February','March','April','May','June',
                'July','August','September','October','November','December']
const DOW = ['Su','Mo','Tu','We','Th','Fr','Sa']

export default function AdminLogCalendar({ selectedDate, onDateChange, activeDates, showLegend = true}: Props) {
  const today = new Date()
  const [isOpen, setIsOpen]     = useState(false)
  const [viewYear, setViewYear] = useState(today.getFullYear())
  const [viewMonth, setViewMonth] = useState(today.getMonth())
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setIsOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const activeDateMap = new Map<string, string>()
  activeDates.forEach(d => activeDateMap.set(d.date, d.severity))

  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate()
  const firstDow    = new Date(viewYear, viewMonth, 1).getDay()

  const prevMonth = () => {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(y => y - 1) }
    else setViewMonth(m => m - 1)
  }
  const nextMonth = () => {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(y => y + 1) }
    else setViewMonth(m => m + 1)
  }

  const handleDayClick = (day: number) => {
    const dateStr = `${viewYear}-${String(viewMonth + 1).padStart(2,'0')}-${String(day).padStart(2,'0')}`
    if (!activeDateMap.get(dateStr)) return
    onDateChange(selectedDate === dateStr ? null : dateStr)
    setIsOpen(false)
  }

  const dotClass = (s: string) =>
    s === 'Error' ? 'alc-dot-err' : s === 'Warning' ? 'alc-dot-warn' : s === 'Success' ? 'alc-dot-ok' : 'alc-dot-info'

  const formatDisplay = () => {
    if (!selectedDate) return 'All dates'
    const d = new Date(selectedDate + 'T00:00:00')
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
  }

  return (
    <div className="alc-wrap" ref={ref}>
      <button
        type="button"
        className={`alc-trigger ${selectedDate ? 'alc-trigger-active' : ''}`}
        onClick={() => setIsOpen(o => !o)}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="4" width="18" height="18" rx="2"/>
          <line x1="16" y1="2" x2="16" y2="6"/>
          <line x1="8" y1="2" x2="8" y2="6"/>
          <line x1="3" y1="10" x2="21" y2="10"/>
        </svg>
        {formatDisplay()}
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </button>

      {isOpen && (
        <div className="alc-dropdown">
          <div className="alc-cal-header">
            <button className="alc-nav" onClick={prevMonth}>‹</button>
            <span className="alc-month-label">{MONTHS[viewMonth]} {viewYear}</span>
            <button className="alc-nav" onClick={nextMonth}>›</button>
          </div>

          <div className="alc-grid">
            {DOW.map(d => <div key={d} className="alc-dow">{d}</div>)}
            {Array.from({ length: firstDow }, (_, i) => <div key={`e${i}`} />)}
            {Array.from({ length: daysInMonth }, (_, i) => {
              const day     = i + 1
              const dateStr = `${viewYear}-${String(viewMonth+1).padStart(2,'0')}-${String(day).padStart(2,'0')}`
              const severity  = activeDateMap.get(dateStr)
              const isSelected = selectedDate === dateStr
              const isToday    = dateStr === today.toISOString().slice(0, 10)
              return (
                <div
                  key={day}
                  className={[
                    'alc-day',
                    isSelected ? 'alc-selected' : '',
                    isToday    ? 'alc-today'    : '',
                    !severity  ? 'alc-empty'    : 'alc-has-activity',
                  ].filter(Boolean).join(' ')}
                  onClick={() => handleDayClick(day)}
                >
                  <span className="alc-num">{day}</span>
                  {severity && <div className={`alc-dot ${dotClass(severity)}`} />}
                </div>
              )
            })}
          </div>

          <div className="alc-footer">
            {(showLegend ?? true) && (
              <div className="alc-legend">
                {([['alc-dot-err','Error'],['alc-dot-warn','Warning'],['alc-dot-ok','Success'],['alc-dot-info','Info']] as [string,string][]).map(([cls, label]) => (
                  <div key={label} className="alc-leg">
                    <div className={`alc-dot ${cls}`} />{label}
                  </div>
                ))}
              </div>
            )}
            {selectedDate && (
              <button className="alc-clear" onClick={() => { onDateChange(null); setIsOpen(false) }}>
                Clear filter
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}