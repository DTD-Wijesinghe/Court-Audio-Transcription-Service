import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import {
  fetchAllUsers, createUser, deleteUserDoc,
  formatDate,
  type FirestoreUser, type CreateUserData
} from '../firebase/userService'
import './AdminDashboard.css'
import Header from '../components/shared/Header'
import Footer from '../components/shared/Footer'
import AdminLogCalendar from '../components/shared/AdminLogCalendar'
import { doc, updateDoc } from 'firebase/firestore'
import { db, auth } from '../firebase/config'
import { fetchCourts, addCourt, deleteCourt, type Court } from '../firebase/courtService'
import { displayError, errorFromResponse } from '../api/systemErrors'
const API_BASE = (import.meta.env.VITE_API_URL ?? '/api').replace(/\/$/, '')

type Tab       = 'dashboard' | 'users' | 'courtManage' | 'activity' | 'syslogs'
type ModalType = 'create' | 'password' | 'delete' | null

const EMPTY_FORM: CreateUserData = {
  displayName: '',
  username:    '',
  email:       '',
  password:    '',
  role:        'Staff User',
  court:       '',
}

function initials(name: string): string {
  return name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase()
}

export default function AdminDashboard() {

  const [activeTab,     setActiveTab]     = useState<Tab>('dashboard')
  const [users,         setUsers]         = useState<FirestoreUser[]>([])
  const [loadingUsers,  setLoadingUsers]  = useState(true)
  const [modal,         setModal]         = useState<ModalType>(null)
  const [selectedUser,  setSelectedUser]  = useState<FirestoreUser | null>(null)
  const [toast,         setToast]         = useState<{ msg: string; type: 'success' | 'error' } | null>(null)

  const [form,        setForm]        = useState<CreateUserData>(EMPTY_FORM)
  const [formError,   setFormError]   = useState('')
  const [formLoading, setFormLoading] = useState(false)
  const [showPw,      setShowPw]      = useState(false)

  const [editCourt,   setEditCourt]   = useState('')
  const [editLoading, setEditLoading] = useState(false)

  const [courts,        setCourts]        = useState<Court[]>([])
  const [loadingCourts, setLoadingCourts] = useState(true)
  const [newCourtName,  setNewCourtName]  = useState('')
  const [addingCourt,   setAddingCourt]   = useState(false)
  const [courtError,    setCourtError]    = useState('')

  const [activityRows,        setActivityRows]        = useState<any[]>([])
  const [activityLoading,     setActivityLoading]     = useState(false)
  const [activityCourtFilter, setActivityCourtFilter] = useState('')
  const [activityError,       setActivityError]       = useState('')
  const [activityLoadedFilter, setActivityLoadedFilter] = useState<string | null>(null)
  const [activityLoaded,       setActivityLoaded]       = useState(false)

  const DEFAULT_PRICES = {
    audioInput:     0.50,
    textInput:      0.15,
    textOutput:     0.60,
    thinkingTokens: 3.50,
    cachedTokens:   0.04,
  }
  const [tokenPrices,  setTokenPrices]  = useState({ ...DEFAULT_PRICES })
  const [pricesOpen,   setPricesOpen]   = useState(false)
  const [pricesDraft,  setPricesDraft]  = useState({ ...DEFAULT_PRICES })
  const [pricesSaving,  setPricesSaving]  = useState(false)
  const [pricesSaved,   setPricesSaved]   = useState(false)
  const pricesRef = useRef<HTMLDivElement>(null)

  const [activityLastLoaded,   setActivityLastLoaded]   = useState<string | null>(null)
  const [activitySelectedDate,  setActivitySelectedDate]  = useState<string | null>(null)
  const [activityActiveDates,   setActivityActiveDates]   = useState<{ date: string; severity: string }[]>([])

  const [sysLogs,           setSysLogs]           = useState<any[]>([])
  const [sysLoading,        setSysLoading]        = useState(false)
  const [sysError,          setSysError]          = useState('')
  const [sysLevelFilter,    setSysLevelFilter]    = useState('')
  const [sysServiceFilter,  setSysServiceFilter]  = useState('')
  const [sysLoaded,         setSysLoaded]         = useState(false)
  const [sysLastLoaded,     setSysLastLoaded]     = useState<string | null>(null)
  const [sysSummary,        setSysSummary]        = useState<any>(null)
  const [systemHealth,      setSystemHealth]      = useState<any>(null)
  const [sysHourly,         setSysHourly]         = useState<any[]>([])
  const [sysSelectedDate, setSysSelectedDate] = useState<string | null>(null)
  const [sysActiveDates,  setSysActiveDates]  = useState<{ date: string; severity: string }[]>([])
  const [sysOffline,        setSysOffline]        = useState(false)
  const [clientOffline, setClientOffline] = useState(false)
  const [sysStatusReady, setSysStatusReady] = useState(false)
  const [sysLiveLevel, setSysLiveLevel] = useState<string | null>(null)

  const loadUsers = useCallback(async () => {
    setLoadingUsers(true)
    try {
      const data = await fetchAllUsers()
      setUsers(data)
    } catch {
      showToast('Failed to load users', 'error')
    } finally {
      setLoadingUsers(false)
    }
  }, [])

  const loadCourts = useCallback(async () => {
  setLoadingCourts(true)
  try {
    const data = await fetchCourts()
    setCourts(data)
  } catch {
    showToast('Failed to load courts', 'error')
  } finally {
    setLoadingCourts(false)
  }
  }, [])

  function slugifyCourt(name: string): string {
    return name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/-{2,}/g, '-')
      .replace(/^-+|-+$/g, '')
  }

  const loadActivityLog = useCallback(async (courtSlug?: string, date?: string | null) => {
    setActivityLoading(true)
    setActivityError('')
    try {
      const token = await auth.currentUser?.getIdToken()
      const params = new URLSearchParams()
      if (courtSlug) params.append('court', courtSlug)
      if (date)      params.append('date', date)
      const query = params.toString() ? `?${params.toString()}` : ''
      const res = await fetch(`${API_BASE}/admin/activity-log${query}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        throw await errorFromResponse(res)
      }
      const data = await res.json()
      setActivityRows(data.rows || [])
      setActivityLastLoaded(new Date().toLocaleTimeString())
    } catch (err) {
      if (!navigator.onLine) {
        setActivityError('No internet connection')
      } else {
        setActivityError(displayError(err))
      }
    } finally {
      setActivityLoading(false)
    }
  }, [])

  const loadSysLogs = useCallback(async (level?: string, service?: string, date?: string | null) => {
    setSysLoading(true)
    setSysError('')
    try {
      const token = await auth.currentUser?.getIdToken()
      const params = new URLSearchParams()
      if (level)   params.append('level', level)
      if (service) params.append('service', service)
      if (date)    params.append('date', date)
      const query = params.toString() ? `?${params.toString()}` : ''
      const res = await fetch(`${API_BASE}/admin/operational-logs${query}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        throw await errorFromResponse(res)
      }
      const data = await res.json()
      setSysOffline(false) 
      setSysLogs(data.logs || [])
      setSysSummary(data.summary || null)
      setSysHourly(data.hourly || [])
      setSysLastLoaded(new Date().toLocaleTimeString())
      setSysLoaded(true)
      if (!date) {
        setSysLiveLevel(data.logs?.[0]?.level || null)
      }
      try {
        const healthRes = await fetch(`${API_BASE}/health`)
        if (healthRes.ok) setSystemHealth(await healthRes.json())
      } catch {}
    } catch (err) {
      setSysError(displayError(err))
      if (!navigator.onLine) {
        setClientOffline(true)
      } else {
        setSysOffline(true)
      }
      setSysLoaded(true)   
    } finally {
      setSysLoading(false)
      setSysStatusReady(true)
    }
  }, [])

  const loadActivityActiveDates = useCallback(async () => {
    try {
      const token = await auth.currentUser?.getIdToken()
      const res = await fetch(`${API_BASE}/admin/activity-log/dates`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return
      const data = await res.json()
      setActivityActiveDates(data.dates || [])
    } catch {}
  }, [])

  const loadSysActiveDates = useCallback(async () => {
    try {
      const token = await auth.currentUser?.getIdToken()
      const res = await fetch(`${API_BASE}/admin/operational-logs/dates`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return
      const data = await res.json()
      setSysActiveDates(data.dates || [])
    } catch {}
  }, [])

  // Browser connectivity listener
  useEffect(() => {
    const handleOnline  = () => {
      setClientOffline(false)
      setSysOffline(false)   // clear stale offline state so ping decides fresh
      setSysError('')
    }
    const handleOffline = () => setClientOffline(true)
    window.addEventListener('online',  handleOnline)
    window.addEventListener('offline', handleOffline)
    return () => {
      window.removeEventListener('online',  handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (pricesRef.current && !pricesRef.current.contains(e.target as Node)) {
        setPricesOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const prevClientOffline = useRef(false)
  useEffect(() => {
    if (prevClientOffline.current && !clientOffline) {
      // Internet just came back — refresh activity log if already loaded
      if (activityLoaded) {
        loadActivityLog(activityCourtFilter || undefined, activitySelectedDate)
      }
      // Clear activity error if it was a connection error
      setActivityError('')
    }
    prevClientOffline.current = clientOffline
  }, [clientOffline])

  useEffect(() => { 
    loadUsers() 
    loadCourts()
    loadActivityActiveDates()
  },[loadUsers, loadCourts, loadActivityActiveDates])

  // Reload activity log when date changes
  useEffect(() => {
    if (activeTab === 'activity' && !activityLoaded) {
      loadActivityLog(activityCourtFilter || undefined, activitySelectedDate)
      setActivityLoadedFilter(activityCourtFilter || '')
      setActivityLoaded(true)

      // Fetch token prices from backend
      auth.currentUser?.getIdToken().then(token =>
        fetch(`${API_BASE}/admin/token-prices`, {
          headers: { Authorization: `Bearer ${token}` }
        })
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (d?.prices) {
            setTokenPrices(d.prices)
            setPricesDraft(d.prices)
          }
        })
        .catch(() => {})
      )
    }
  }, [activeTab, activityLoaded, loadActivityLog])

  // auto-refresh every 2 minutes
  useEffect(() => {
    const interval = setInterval(() => {
      if (activityLoadedFilter !== null) {
        const courtSlug = activityLoadedFilter || undefined
        auth.currentUser?.getIdToken().then(token => {
          const params = new URLSearchParams()
          if (courtSlug)            params.append('court', courtSlug)
          if (activitySelectedDate) params.append('date', activitySelectedDate)
          const query = params.toString() ? `?${params.toString()}` : ''
          fetch(`${API_BASE}/admin/activity-log${query}`, {
            headers: { Authorization: `Bearer ${token}` },
          })
            .then(res => res.ok ? res.json() : null)
            .then(data => {
              if (data?.rows) {
                setActivityRows(data.rows)
                setActivityLastLoaded(new Date().toLocaleTimeString())
              }
            })
            .catch(() => {})
        })
      }
    }, 2 * 60 * 1000)

    return () => clearInterval(interval)
  }, [activityLoadedFilter, activitySelectedDate])

  useEffect(() => {
    if (activityLoaded) {
      loadActivityLog(activityCourtFilter || undefined, activitySelectedDate)
      setActivityLoadedFilter(activityCourtFilter || '')
    }
  }, [activityCourtFilter, activitySelectedDate])

  // Load on first visit, cache on tab switch
  useEffect(() => {
    if (activeTab === 'syslogs' && !sysLoaded) {
      loadSysLogs(sysLevelFilter || undefined, sysServiceFilter || undefined, sysSelectedDate)
      loadSysActiveDates()
    }
  }, [activeTab, sysLoaded, loadSysLogs, loadSysActiveDates])

  // Reload when filters change
  useEffect(() => {
    if (activeTab === 'syslogs' && sysLoaded) {
      loadSysLogs(sysLevelFilter || undefined, sysServiceFilter || undefined, sysSelectedDate)
      loadSysActiveDates()
    }
  }, [sysLevelFilter, sysServiceFilter, sysSelectedDate])

  // auto-refresh every 2 minutes
  useEffect(() => {
    const interval = setInterval(() => {
      if (sysLoaded) {
        auth.currentUser?.getIdToken().then(token => {
          const params = new URLSearchParams()
          if (sysLevelFilter)   params.append('level', sysLevelFilter)
          if (sysServiceFilter) params.append('service', sysServiceFilter)
          if (sysSelectedDate)  params.append('date', sysSelectedDate)
          const query = params.toString() ? `?${params.toString()}` : ''
          fetch(`${API_BASE}/admin/operational-logs${query}`, {
            headers: { Authorization: `Bearer ${token}` },
          })
            .then(res => {
              if (!res.ok) {
                setSysOffline(true)
                return null
              }
              return res.json()
            })
            .then(data => {
              if (data?.logs) {
                setSysOffline(false)
                setSysLogs(data.logs)
                setSysSummary(data.summary || null)
                setSysHourly(data.hourly || [])
                setSysLastLoaded(new Date().toLocaleTimeString())
                loadSysActiveDates()
              }
            })
            .catch(() => {
              if (!navigator.onLine) {
                setClientOffline(true)
              } else {
                setSysOffline(true)
              }
            })
        })
      }
    }, 2 * 60 * 1000)
    return () => clearInterval(interval)
  }, [sysLoaded, sysLevelFilter, sysServiceFilter, sysSelectedDate])

  // ── Status ping every 30 seconds ──
  useEffect(() => {
    if (activeTab !== 'syslogs') return
    const interval = setInterval(async () => {
      if (clientOffline) return
      try {
        const token = await auth.currentUser?.getIdToken()
        const res = await fetch(`${API_BASE}/admin/operational-logs`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (res.ok) {
          const data = await res.json()
          setSysError('') 
          setSysOffline(false)
          setClientOffline(false)
          setSysLiveLevel(data.logs?.[0]?.level || null)
          setSysLastLoaded(new Date().toLocaleTimeString())
          
          try {
            const healthRes = await fetch(`${API_BASE}/health`)
            if (healthRes.ok) setSystemHealth(await healthRes.json())
          } catch {}
          
        } else {
          setSysOffline(true)
        }
      } catch {
        if (!navigator.onLine) {
          setClientOffline(true)
        } else {
          setSysOffline(true)
        }
      }
    }, 30 * 1000)
    return () => clearInterval(interval)
  }, [activeTab, sysOffline])

  function exportActivityCsv() {
    const headers = [
      'Court', 'Case Number', 'Hearing Date', 'Uploaded On', 'Uploaded By', 'Duration',
      'Total Tokens',
      'Transcript - Audio In', 'Transcript - Text In', 'Transcript - Cached (10% rate)', 'Transcript - Text Out', 'Transcript - Thinking', 'Transcript - Est. Cost ($)',
      'Proceeding - Text In', 'Proceeding - Cached (10% rate)', 'Proceeding - Text Out', 'Proceeding - Thinking', 'Proceeding - Est. Cost ($)',
      'Sinhala - Text In', 'Sinhala - Cached (10% rate)', 'Sinhala - Text Out', 'Sinhala - Thinking', 'Sinhala - Est. Cost ($)',
      'Tamil - Text In', 'Tamil - Cached (10% rate)', 'Tamil - Text Out', 'Tamil - Thinking', 'Tamil - Est. Cost ($)',
      'English - Text In', 'English - Cached (10% rate)', 'English - Text Out', 'English - Thinking', 'English - Est. Cost ($)',
      'Total Est. Cost ($)',
      'Transcript', 'Finalized', 'Proceeding', 'Sinhala', 'Tamil', 'English',
    ]
    const csvRows = activityRows.map(r => {
      const bd: any[] = r.tokenBreakdown || []
      const cat = (label: string) => bd.find((b: any) => b.label === label) || {}
      const orig = cat('Transcript')
      const proc = cat('Proceeding')
      const si   = cat('Sinhala')
      const ta   = cat('Tamil')
      const en   = cat('English')

      const origCost = calcCost(orig)
      const procCost = calcCost(proc)
      const siCost   = calcCost(si)
      const taCost   = calcCost(ta)
      const enCost   = calcCost(en)
      const totalCost = origCost + procCost + siCost + taCost + enCost
      return [
        r.court, r.caseNumber, r.hearingDate, r.savedAt ?? '-', r.uploadedBy, r.audioDurationFormatted,
        r.totalTokens,
        orig.audioInput ?? 0, orig.textInput ?? 0, orig.cachedTokens ?? 0, orig.textOutput ?? 0, orig.thinkingTokens ?? 0, origCost.toFixed(4),
        proc.textInput ?? 0, proc.cachedTokens ?? 0, proc.textOutput ?? 0, proc.thinkingTokens ?? 0, procCost.toFixed(4),
        si.textInput ?? 0, si.cachedTokens ?? 0, si.textOutput ?? 0, si.thinkingTokens ?? 0, siCost.toFixed(4),
        ta.textInput ?? 0, ta.cachedTokens ?? 0, ta.textOutput ?? 0, ta.thinkingTokens ?? 0, taCost.toFixed(4),
        en.textInput ?? 0, en.cachedTokens ?? 0, en.textOutput ?? 0, en.thinkingTokens ?? 0, enCost.toFixed(4),
        totalCost.toFixed(4),
        'Yes', r.finalized ? 'Yes' : 'No', r.proceedingGenerated ? 'Yes' : 'No',
        r.translations?.sinhala ? 'Yes' : 'No', r.translations?.tamil ? 'Yes' : 'No', r.translations?.english ? 'Yes' : 'No',
      ]
    })
    const csv = [headers, ...csvRows].map(row => row.map(v => `"${v}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `activity_log_${activitySelectedDate ?? 'all_dates'}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

    function exportLogsCsv() {
    const headers = [
      'Time', 'Level', 'Service', 'Event', 'Detail', 'Duration (s)', 'Triggered By', 'Court', 'Job / Case',
    ]
    const csvRows = sysLogs.map(log => [
      log.time ?? '-',
      log.level ?? '-',
      log.service ?? '-',
      log.event ?? '-',
      log.detail ?? '-',
      log.durationSeconds > 0 ? log.durationSeconds : '-',
      log.uploadedBy ?? 'System',
      log.court ?? '-',
      log.caseNumber ?? '-',
    ])
    const csv = [headers, ...csvRows].map(row => row.map(v => `"${v}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `operational_logs_${sysSelectedDate ?? 'all_dates'}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }
  

  function showToast(msg: string, type: 'success' | 'error' = 'success') {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 2800)
  }

  function openCreate() {
    setForm({ ...EMPTY_FORM, court: courts[0]?.name || '' })
    setFormError('')
    setShowPw(false)
    setModal('create')
  }

  function openEdit(u: FirestoreUser) {
    setSelectedUser(u)
    setEditCourt(u.court || courts[0]?.name || '')
    setFormError('')
    setModal('password')
  }

  function openDelete(u: FirestoreUser) {
    setSelectedUser(u)
    setModal('delete')
  }

  function closeModal() {
    setModal(null)
    setSelectedUser(null)
    setFormError('')
  }

  async function handleCreateUser() {
    setFormError('')

    if (!form.displayName.trim()) { setFormError('Full name is required.');  return }
    if (!form.username.trim())    { setFormError('Username is required.');    return }
    if (!form.email.trim())        { setFormError('Email is required.');      return }
    if (!form.email.includes('@')) { setFormError('Please enter a valid email.');   return }
    if (!form.password.trim())    { setFormError('Password is required.');    return }
    if (form.password.length < 6) { setFormError('Password must be at least 6 characters.'); return }
    if (form.role === 'Staff User' && !form.court) {
      setFormError('Please select a court.')
      return
    }

    setFormLoading(true)
    try {
      await createUser(form)
      await loadUsers()
      closeModal()
      showToast('User created successfully')
    } catch (err: unknown) {
      const code = (err as { code?: string }).code
      if (code === 'auth/email-already-in-use') {
        setFormError('Email already exists. Choose a different one.')
      } else {
        setFormError('Failed to create user. Please try again.')
      }
    } finally {
      setFormLoading(false)
    }
  }

  async function handleDeleteUser() {
    if (!selectedUser) return
    setFormLoading(true)
    try {
      await deleteUserDoc(selectedUser.uid)
      await loadUsers()
      closeModal()
      showToast('User deleted successfully')
    } catch {
      showToast('Failed to delete user', 'error')
    } finally {
      setFormLoading(false)
    }
  }

  async function handleEditUser() {
    if (!selectedUser) return
    setEditLoading(true)
    try {
      console.log('Updating UID:', selectedUser.uid)
      console.log('New court:', editCourt)
      console.log('Current user auth:', auth.currentUser?.uid)
      
      await updateDoc(doc(db, 'users', selectedUser.uid), {
        court: editCourt,
      })
      console.log('✅ Update success')
      await loadUsers()
      closeModal()
      showToast('Court updated successfully')
    } catch (err) {
      console.log('❌ Full error:', JSON.stringify(err))
      console.log('❌ Error message:', (err as Error).message)
      showToast('Failed to update court', 'error')
    } finally {
      setEditLoading(false)
    }
  }

  async function handleAddCourt() {
    setCourtError('')
    if (!newCourtName.trim()) {
      setCourtError('Please enter a court name.')
      return
    }
    if (courts.some(c => c.name.toLowerCase() === newCourtName.trim().toLowerCase())) {
      setCourtError('This court already exists.')
      return
    }
    setAddingCourt(true)
    try {
      await addCourt(newCourtName)
      await loadCourts()
      setNewCourtName('')
      showToast('Court added successfully')
    } catch {
      showToast('Failed to add court', 'error')
    } finally {
      setAddingCourt(false)
    }
  }

  async function handleDeleteCourt(court: Court) {
    const hasUsers = users.some(u => u.court === court.name)
    if (hasUsers) {
      showToast(`Cannot delete - ${court.name} has users assigned`, 'error')
      return
    }
    try {
      await deleteCourt(court.id)
      await loadCourts()
      showToast('Court removed successfully')
    } catch {
      showToast('Failed to remove court', 'error')
    }
  }

  const saveTokenPrices = async () => {
    setPricesSaving(true)
    try {
      const token = await auth.currentUser?.getIdToken()
      const res = await fetch(`${API_BASE}/admin/token-prices`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(pricesDraft),
      })
      if (res.ok) {
        const d = await res.json()
        setTokenPrices(d.prices)
        setPricesDraft(d.prices)
        setPricesSaved(true)
        setTimeout(() => setPricesSaved(false), 2000)
      }
    } catch {}
    setPricesSaving(false)
  }
  const calcCost = (b: any): number => {
    return (
      (b.audioInput     || 0) * tokenPrices.audioInput +
      (b.textInput      || 0) * tokenPrices.textInput +
      (b.textOutput     || 0) * tokenPrices.textOutput +
      (b.thinkingTokens || 0) * tokenPrices.thinkingTokens +
      (b.cachedTokens   || 0) * tokenPrices.cachedTokens
    ) / 1_000_000
  }

  const costSummary = useMemo(() => {
    let transcriptProceeding = 0
    let translations = 0
    const courtSet = new Set<string>()
    activityRows.forEach(r => {
      courtSet.add(r.court)
      ;(r.tokenBreakdown || []).forEach((b: any) => {
        const cost = calcCost(b)
        if (b.label === 'Transcript' || b.label === 'Proceeding') {
          transcriptProceeding += cost
        } else {
          translations += cost
        }
      })
    })
    return {
      transcriptProceeding,
      translations,
      total: transcriptProceeding + translations,
      jobs: activityRows.length,
      courts: courtSet.size,
    }
  }, [activityRows, tokenPrices])

  const costSummaryDateLabel = activitySelectedDate
    ? new Date(activitySelectedDate + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
    : (() => {
        const dates = activityRows.map(r => r.savedAt?.slice(0, 10)).filter(Boolean).sort()
        if (!dates.length) return 'All time'
        const fmt = (d: string) => new Date(d + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
        return dates[0] === dates[dates.length - 1] ? fmt(dates[0]) : `${fmt(dates[0])} - ${fmt(dates[dates.length - 1])}`
      })()

  const staffCount = users.filter(u => u.role === 'Staff User').length
  const adminCount = users.filter(u => u.role === 'System Administrator').length

  const sysStatus = clientOffline
    ? { cls: 'sys-pill-off',  dotCls: '',              text: 'No internet connection'  }
    : sysOffline
    ? { cls: 'sys-pill-off',  dotCls: '',              text: 'Server offline'          }
    : systemHealth?.temporaryStorage?.status === 'critical'
    ? { cls: 'sys-pill-err', dotCls: 'live-dot-err', text: 'Server storage critical' }
    : systemHealth?.temporaryStorage?.status === 'warning'
    ? { cls: 'sys-pill-warn', dotCls: 'live-dot-warn', text: 'Server storage warning' }
    : sysLiveLevel === 'Error'
    ? { cls: 'sys-pill-err',  dotCls: 'live-dot-err',  text: 'System error detected'         }
    : sysLiveLevel === 'Warning'
    ? { cls: 'sys-pill-warn', dotCls: 'live-dot-warn', text: 'Degraded performance'       }
    : { cls: 'sys-pill-ok',   dotCls: 'live-dot',      text: 'System operational' }
  return (
    <div className="admin-page">

      {/* ── Header ── */}
        <Header />

      {/* ── Nav tabs ── */}
      <nav className="admin-nav">
        {(['dashboard', 'users', 'courtManage', 'activity', 'syslogs'] as Tab[]).map(tab => (
          <div
            key={tab}
            className={`admin-nav-item ${activeTab === tab ? 'active' : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === 'dashboard' && 'Dashboard'}
            {tab === 'users'     && 'User management'}
            {tab === 'courtManage'   && 'Court management'}
            {tab === 'activity'  && 'Activity logs'}
            {tab === 'syslogs'   && 'Operational logs'}
          </div>
        ))}
      </nav>

      {/* ── Main ── */}
      <main className="admin-main">

        {/* ════ DASHBOARD TAB ════ */}
        {activeTab === 'dashboard' && (
          <>
            {/* Stats */}
            <div className="stat-grid">
              <div className="stat-card">
                <div className="stat-label">Total users</div>
                <div className="stat-val">{users.length}</div>
                <div className="stat-sub">Across all courts</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Staff users</div>
                <div className="stat-val">{staffCount}</div>
                <div className="stat-sub">Court staff accounts</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Administrators</div>
                <div className="stat-val">{adminCount}</div>
                <div className="stat-sub">System admin accounts</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Active courts</div>
                <div className="stat-val">{courts.length}</div>
                <div className="stat-sub">All namespaces ready</div>
              </div>
            </div>

            {/* Recent users preview */}
            <div className="admin-section">
              <div className="admin-section-header">
                <div>
                  <div className="admin-section-title">Recent users</div>
                  <div className="admin-section-sub">Latest accounts created</div>
                </div>
                <button type="button" className="btn-primary" onClick={() => { openCreate(); setActiveTab('users') }}>
                  + Create user
                </button>
              </div>
              <div>
                {loadingUsers ? (
                  <div className="spinner-wrap">Loading users...</div>
                ) : users.length === 0 ? (
                  <div className="empty-state">No users found. Create your first user.</div>
                ) : (
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>User</th>
                        <th>Username</th>
                        <th>Email</th>
                        <th>Role</th>
                        <th>Court</th>
                        <th>Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.slice(0, 5).map(u => (
                        <tr key={u.uid}>
                          <td>
                            <div className="user-cell">
                              <span className="avatar">{initials(u.displayName)}</span>
                              {u.displayName}
                            </div>
                          </td>
                          <td><span className="mono">{u.username}</span></td>
                          <td><span className="mono">{u.email}</span></td>
                          <td>
                            <span className={`tag ${u.role === 'System Administrator' ? 'tag-blue' : u.role === 'Judge' ? 'tag-purple' : 'tag-green'}`}>
                              {u.role}
                            </span>
                          </td>
                          <td>{u.court || '-'}</td>
                          <td className="text-muted">{formatDate(u.createdAt)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </>
        )}

        {/* ════ USER MANAGEMENT TAB ════ */}
        {activeTab === 'users' && (
          <div className="admin-section">
            <div className="admin-section-header">
              <div>
                <div className="admin-section-title">User management</div>
                <div className="admin-section-sub">Create, edit and remove court staff accounts</div>
              </div>
              <button type="button" className="btn-primary" onClick={openCreate}>+ Create user</button>
            </div>

            {loadingUsers ? (
              <div className="spinner-wrap">Loading users...</div>
            ) : users.length === 0 ? (
              <div className="empty-state">No users found. Create your first user.</div>
            ) : (
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Username</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Court</th>
                    <th>Created</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map(u => (
                    <tr key={u.uid}>
                      <td>
                        <div className="user-cell">
                          <span className="avatar">{initials(u.displayName)}</span>
                          {u.displayName}
                        </div>
                      </td>
                      <td><span className="mono">{u.username}</span></td>
                      <td><span className="mono">{u.email}</span></td>
                      <td>
                        <span className={`tag ${u.role === 'System Administrator' ? 'tag-blue' : u.role === 'Judge' ? 'tag-purple' : 'tag-green'}`}>
                          {u.role}
                        </span>
                      </td>
                      <td>{u.court || '-'}</td>
                      <td className="text-muted">{formatDate(u.createdAt)}</td>
                      <td>
                        <div className="table-actions">
                          <button
                            type="button"
                            className="btn-icon"
                            onClick={() => openEdit(u)}
                            title="Change court"
                          >
                            <svg width="12" height="12" viewBox="0 0 24 24"
                                fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/>
                              <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
                            </svg>
                            Change court
                          </button>
                          <button type="button" className="btn-danger" onClick={() => openDelete(u)}>
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* ════ COURT MANAGEMENT TAB ════ */}
        {activeTab === 'courtManage' && (
          <div className="admin-section">
            <div className="admin-section-header">
              <div>
                <div className="admin-section-title">Court management</div>
                <div className="admin-section-sub">
                  Manage court folders in object storage
                </div>
              </div>
            </div>

            {/* ── Add new court ── */}
            <div className="storage-add-section">
              <div className="storage-add-title">Add new court</div>
              <div className="storage-add-row">
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Appeal Court"
                  value={newCourtName}
                  onChange={e => {
                    setNewCourtName(e.target.value)
                    setCourtError('')
                  }}
                  onKeyDown={e => { if (e.key === 'Enter') handleAddCourt() }}
                />
                <button
                  type="button"
                  className="btn-primary"
                  onClick={handleAddCourt}
                  disabled={addingCourt}
                >
                  {addingCourt ? 'Adding...' : '+ Add court'}
                </button>
              </div>
              {courtError && (
                <div className="form-error" style={{ marginTop: '8px', marginBottom: 0 }}>
                  {courtError}
                </div>
              )}
            </div>

            {/* ── Court list ── */}
            {loadingCourts ? (
              <div className="spinner-wrap">Loading courts...</div>
            ) : courts.length === 0 ? (
              <div className="empty-state">No courts found. Add your first court above.</div>
            ) : (
              <div className="court-grid">
                {courts.map(court => {
                  const courtUsers = users.filter(u => u.court === court.name)
                  return (
                    <div key={court.id} className="court-card exists">
                      <div className="court-name">{court.name}</div>
                      <div className="court-status">
                        {courtUsers.length} user{courtUsers.length !== 1 ? 's' : ''} assigned
                      </div>
                      <div className="court-actions">
                        <button
                          type="button"
                          className="btn-danger btn-sm"
                          onClick={() => handleDeleteCourt(court)}
                          title={courtUsers.length > 0 ? 'Cannot delete - users assigned' : 'Delete court'}
                          disabled={courtUsers.length > 0}
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {/* ════ ACTIVITY LOG TAB ════ */}
        {activeTab === 'activity' && (
          <div className="admin-section">
            <div className="admin-section-header">
              <div>
                <div className="admin-section-title">Activity logs</div>
                <div className="admin-section-sub">File processing history across all courts</div>
              </div>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                {/* Last loaded timestamp */}
                {activityLastLoaded && (
                  <span className="text-muted">
                    Loaded at {activityLastLoaded}
                  </span>
                )}

                {/* Manual refresh button */}
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => loadActivityLog(activityCourtFilter || undefined, activitySelectedDate)}
                  disabled={activityLoading}
                  style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 14px' }}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2">
                    <polyline points="23 4 23 10 17 10"/>
                    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                  </svg>
                  {activityLoading ? 'Loading...' : 'Refresh'}
                </button>

                <select
                  className="form-select"
                  style={{ width: '200px' }}
                  value={activityCourtFilter}
                  onChange={e => setActivityCourtFilter(e.target.value)}
                >
                  <option value="">All courts</option>
                  {courts.map(c => (
                    <option key={c.id} value={slugifyCourt(c.name)}>{c.name}</option>
                  ))}
                </select>
                <AdminLogCalendar
                  selectedDate={activitySelectedDate}
                  onDateChange={setActivitySelectedDate}
                  activeDates={activityActiveDates}
                  showLegend={false}
                />

                {/* Token prices dropdown */}
                <div ref={pricesRef} style={{ position: 'relative' }}>
                  <button
                    type="button"
                    className={`btn-outline-blue${pricesOpen ? ' active' : ''}`}
                    onClick={() => { setPricesOpen(p => !p); setPricesSaved(false) }}
                    style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '7px 13px' }}
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <rect x="4" y="2" width="16" height="20" rx="2"/>
                      <rect x="7" y="5" width="10" height="3" rx="0.5"/>
                      <circle cx="8" cy="12" r="1" fill="currentColor" stroke="none"/>
                      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>
                      <circle cx="16" cy="12" r="1" fill="currentColor" stroke="none"/>
                      <circle cx="8" cy="16" r="1" fill="currentColor" stroke="none"/>
                      <circle cx="12" cy="16" r="1" fill="currentColor" stroke="none"/>
                      <circle cx="16" cy="16" r="1" fill="currentColor" stroke="none"/>
                    </svg>
                    Token prices
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                      <polyline points="6 9 12 15 18 9"/>
                    </svg>
                  </button>

                  {pricesOpen && (
                    <div className="al-prices-dropdown">
                      <div className="al-prices-head">
                        <div className="al-prices-title">Token pricing</div>
                        <div className="al-prices-sub">USD per 1 million tokens · shared across all admins</div>
                      </div>
                      <div className="al-prices-body">
                        {([
                          ['audioInput',     'Audio input tokens'],
                          ['textInput',      'Text input tokens'],
                          ['textOutput',     'Text output tokens'],
                          ['thinkingTokens', 'Thinking tokens'],
                          ['cachedTokens',   'Cached tokens'],
                        ] as [keyof typeof pricesDraft, string][]).map(([key, label]) => (
                          <div key={key} className="al-price-row">
                            <span className="al-price-label">{label}</span>
                            <div className="al-price-input-wrap">
                              <span className="al-price-sym">$</span>
                              <input
                                type="number"
                                min="0"
                                step="0.01"
                                className="al-price-input"
                                value={pricesDraft[key]}
                                onChange={e => setPricesDraft(p => ({
                                  ...p,
                                  [key]: parseFloat(e.target.value) || 0
                                }))}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                      <div className="al-prices-note">Costs shown in the table are estimates only.</div>
                      <div className="al-prices-footer">
                        <button
                          className="al-prices-reset"
                          onClick={() => setPricesDraft({ ...DEFAULT_PRICES })}
                        >
                          Reset to defaults
                        </button>
                        <button
                          className="al-prices-save"
                          onClick={saveTokenPrices}
                          disabled={pricesSaving}
                        >
                          {pricesSaved ? '✓ Saved' : pricesSaving ? 'Saving...' : 'Save prices'}
                        </button>
                      </div>
                    </div>
                  )}
                </div>

                <button type="button" className="btn-primary" onClick={exportActivityCsv} disabled={!activityRows.length}
                  style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 16px' }}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                  </svg>
                  Export CSV
                </button>
              </div>
            </div>

            {activityRows.length > 0 && (
              <div className="cost-summary-row">
                <div className="cost-card">
                  <div className="cost-icon blue">
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                      <polyline points="14 2 14 8 20 8"/>
                      <line x1="16" y1="13" x2="8" y2="13"/>
                      <line x1="16" y1="17" x2="8" y2="17"/>
                    </svg>
                  </div>
                  <div className="cost-card-body">
                    <div className="cost-card-label">Transcript + Proceeding</div>
                    <div className="cost-card-val">${costSummary.transcriptProceeding.toFixed(4)}</div>
                    <div className="cost-card-meta">Core processing cost</div>
                  </div>
                </div>

                <div className="cost-card">
                  <div className="cost-icon amber">
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>
                      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                    </svg>
                  </div>
                  <div className="cost-card-body">
                    <div className="cost-card-label">Translations</div>
                    <div className="cost-card-val">${costSummary.translations.toFixed(4)}</div>
                    <div className="cost-card-meta">Sinhala · Tamil · English</div>
                  </div>
                </div>

                <div className="cost-card">
                  <div className="cost-icon slate">
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
                    </svg>
                  </div>
                  <div className="cost-card-body">
                    <div className="cost-card-label">Jobs processed</div>
                    <div className="cost-card-val">{costSummary.jobs}</div>
                    <div className="cost-card-meta">Across {costSummary.courts} court{costSummary.courts !== 1 ? 's' : ''}</div>
                  </div>
                </div>

                <div className="cost-card total">
                  <div className="cost-icon">
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2">
                      <ellipse cx="12" cy="6" rx="8" ry="3"/>
                      <path d="M4 6v4c0 1.66 3.58 3 8 3s8-1.34 8-3V6"/>
                      <path d="M4 10v4c0 1.66 3.58 3 8 3s8-1.34 8-3v-4"/>
                    </svg>
                  </div>
                  <div className="cost-card-body">
                    <div className="cost-card-label">
                      Total cost<span className="badge-pill">{activitySelectedDate ? 'SELECTED DAY' : 'ALL DATES'}</span>
                    </div>
                    <div className="cost-card-val">${costSummary.total.toFixed(4)}</div>
                    <div className="cost-card-meta">{costSummaryDateLabel}</div>
                  </div>
                </div>
              </div>
            )}

            {activityLoading ? (
              <div className="spinner-wrap">Loading activity log...</div>
            ) : activityError ? (
              <div className="empty-state">{activityError}</div>
            ) : activityRows.length === 0 ? (
              <div className="empty-state">No processed files found.</div>
            ) : (
              <div className="activity-table-scroll" role="region" aria-label="Activity log table" tabIndex={0}>
              <table className="admin-table al-table">
                <colgroup>
                  <col style={{ width: '200px' }} />{/* Court */}
                  <col style={{ width: '205px' }} />{/* Case number */}
                  <col style={{ width: '115px' }} />{/* Hearing date */}
                  <col style={{ width: '115px' }} />{/* Uploaded on */}
                  <col style={{ width: '180px' }} />{/* Uploaded by */}
                  <col style={{ width: '90px' }}  />{/* Duration */}
                  <col style={{ width: '250px' }} />{/* Tokens */}
                  <col />
                </colgroup>
                <thead>
                  <tr>
                    <th>Court</th>
                    <th>Case number</th>
                    <th>Hearing date</th>
                    <th>Uploaded on</th>
                    <th>Uploaded by</th>
                    <th>Duration</th>
                    <th>Tokens | Estimated Usage Cost($)</th>
                    <th>Outputs generated</th>
                  </tr>
                </thead>
                <tbody>
                  {activityRows.map((r, i) => (
                    <tr key={i}>
                      <td>{r.court}</td>
                      <td className="mono">{r.caseNumber}</td>
                      <td>{r.hearingDate}</td>
                      <td>
                        <div>{r.savedAt ? r.savedAt.slice(0, 10) : '-'}</div>
                        <div className="al-tok-break">{r.savedAt ? r.savedAt.slice(11, 16) : ''}</div>
                      </td>
                      <td>{r.uploadedBy}</td>
                      <td className="mono">{r.audioDurationFormatted}</td>
                      <td>
                        <div className="al-tok-total">{r.totalTokens.toLocaleString()}</div>
                        {(r.tokenBreakdown || []).map((b: any) => {
                          const cost = calcCost(b)
                          return (
                            <div className="al-tok-break al-tok-break-row" key={b.label}>
                              <div className="al-tok-break-left">
                                <strong>{b.label}:</strong>{' '}
                                {b.audioInput > 0 && <>{b.audioInput.toLocaleString()} audio in &nbsp;·&nbsp;</>}
                                {b.textInput.toLocaleString()} txt in
                                {b.cachedTokens > 0 && (
                                  <span className="al-tok-cached">
                                    &nbsp;({b.cachedTokens.toLocaleString()} cached)
                                  </span>
                                )}
                                &nbsp;·&nbsp;{b.textOutput.toLocaleString()} txt out
                                {b.thinkingTokens > 0 && (
                                  <>&nbsp;·&nbsp;{b.thinkingTokens.toLocaleString()} thinking</>
                                )}
                              </div>
                              <span className="al-tok-cost">${cost.toFixed(4)}</span>
                            </div>
                          )
                        })}
                        {(r.tokenBreakdown || []).length > 0 && (() => {
                          const total = (r.tokenBreakdown || []).reduce(
                            (sum: number, b: any) => sum + calcCost(b), 0
                          )
                          return (
                            <div className="al-tok-total-cost">
                              <span>Total est. cost</span>
                              <span>${total.toFixed(4)}</span>
                            </div>
                          )
                        })()}
                      </td>
                      <td>
                        <div className="al-badges">

                          {/* Original download */}
                          <span className="al-badge on-blue">
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                              <polyline points="14 2 14 8 20 8"/>
                              <line x1="16" y1="13" x2="8" y2="13"/>
                              <line x1="16" y1="17" x2="8" y2="17"/>
                            </svg>
                            Transcript
                          </span>

                          {/* Finalized download */}
                          <span className={`al-badge ${r.finalized ? 'on-green' : 'off'}`}>
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                              <polyline points="14 2 14 8 20 8"/>
                              {r.finalized && <polyline points="8 14 11 17 16 12" strokeWidth="2.5"/>}
                            </svg>
                            Finalized
                          </span>

                          {/* Proceeding download */}
                          <span className={`al-badge ${r.proceedingGenerated ? 'on-green' : 'off'}`}>
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                            </svg>
                            Proceeding
                          </span>

                          {/* Translations download */}
                          <span className={`al-badge ${r.translations?.sinhala ? 'on-amber' : 'off'}`}>
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <circle cx="12" cy="12" r="10"/>
                              <line x1="2" y1="12" x2="22" y2="12"/>
                              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                            </svg>
                            Sinhala
                          </span>
                          <span className={`al-badge ${r.translations?.tamil ? 'on-amber' : 'off'}`}>
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <circle cx="12" cy="12" r="10"/>
                              <line x1="2" y1="12" x2="22" y2="12"/>
                              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                            </svg>
                            Tamil
                          </span>
                          <span className={`al-badge ${r.translations?.english ? 'on-amber' : 'off'}`}>
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <circle cx="12" cy="12" r="10"/>
                              <line x1="2" y1="12" x2="22" y2="12"/>
                              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                            </svg>
                            English
                          </span>

                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </div>
        )}

        {/* ════ OPERATIONAL LOGS TAB ════ */}
        {activeTab === 'syslogs' && (
          <div className="admin-section">
            <div className="admin-section-header">
              <div>
                <div className="admin-section-title">Operational logs</div>
                <div className="admin-section-sub">Server events, errors and system health across all services</div>
              </div>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                {sysStatusReady && (
                  <div className={`sys-status-pill ${sysStatus.cls}`}>
                    <div className={`sys-status-dot ${sysStatus.dotCls}`} />
                    {sysStatus.text}
                  </div>
                )}
                {/* Last loaded timestamp */}
                {sysLastLoaded && (
                  <span className="text-muted">
                    Updated at {sysLastLoaded}
                  </span>
                )}

                {/* Manual refresh button */}
                <button 
                  type="button" 
                  className="btn-primary"
                  onClick={() => {loadSysLogs(sysLevelFilter || undefined, sysServiceFilter || undefined, sysSelectedDate); loadSysActiveDates()}}
                  disabled={sysLoading}
                  style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 14px' }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2.5">
                    <polyline points="23 4 23 10 17 10"/>
                    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                  </svg>
                  {sysLoading ? 'Loading...' : 'Refresh'}
                </button>

                <select className="form-select" style={{ width: '130px', fontWeight: 490}}
                  value={sysLevelFilter} onChange={e => setSysLevelFilter(e.target.value)}>
                  <option value="">All levels</option>
                  <option value="Error">Error</option>
                  <option value="Warning">Warning</option>
                  <option value="Success">Success</option>
                  <option value="Info">Info</option>
                </select>
                <select className="form-select" style={{ width: '130px', fontWeight: 490}}
                  value={sysServiceFilter} onChange={e => setSysServiceFilter(e.target.value)}>
                  <option value="">All services</option>
                  <option value="API Server">API Server</option>
                  <option value="Vertex AI">Vertex AI</option>
                  <option value="GCS">GCS</option>
                  <option value="S3">S3</option>
                  <option value="Audio Processing">Audio Processing</option>
                  <option value="Poller">Poller</option>
                  <option value="Temporary Storage">Temporary Storage</option>
                  <option value="Queue">Queue</option> 
                </select>

                <AdminLogCalendar
                  selectedDate={sysSelectedDate}
                  onDateChange={setSysSelectedDate}
                  activeDates={sysActiveDates}
                />

                <button type="button" className="btn-primary" onClick={exportLogsCsv} disabled={!sysLogs.length}
                  style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 16px' }}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                  </svg>
                  Export CSV
                </button>
              </div>
            </div>

            {systemHealth?.temporaryStorage && (
              <div className="sys-stats-row">
                <div className="sys-stat">
                  <div className="sys-stat-dot" style={{ background: systemHealth.temporaryStorage.status === 'healthy' ? '#7cca3c' : systemHealth.temporaryStorage.status === 'warning' ? '#dcbe5a' : '#d84343' }}/>
                  <span className="sys-stat-label">Server disk</span>
                  <span className="sys-stat-val" style={{ color: '#6B7280' }}>{systemHealth.temporaryStorage.usedPercent}% used</span>
                </div>
                <div className="sys-stat"><span className="sys-stat-label">Disk free</span><span className="sys-stat-val" style={{ color: '#6B7280' }}>{systemHealth.temporaryStorage.freeGb} GB</span></div>
                <div className="sys-stat"><span className="sys-stat-label">Temporary files</span><span className="sys-stat-val" style={{ color: '#6B7280' }}>{(systemHealth.temporaryStorage.temporaryBytes / 1048576).toFixed(1)} MB</span></div>
                <div className="sys-stat">
                  <span className="sys-stat-label">Active audio jobs</span>
                  <span className="sys-stat-val" style={{ color: '#6B7280' }}>
                    {systemHealth.audioProcessing?.activeJobs ?? 0}
                  </span>
                </div>
                <div className="sys-stat">
                  <span className="sys-stat-label">Queued audio jobs</span>
                  <span className="sys-stat-val" style={{ color: '#6B7280' }}>
                    {systemHealth.audioProcessing?.queuedJobs ?? 0}
                  </span>
                </div>
                <div className="sys-stat"><span className="sys-stat-label">Storage status</span><span className="sys-stat-val" style={{ textTransform: 'capitalize', color: '#6B7280' }}>{systemHealth.temporaryStorage.status}</span></div>
              </div>
            )}
            
            {/*bar chart and donut chart*/}
            {sysSummary && (
               <div className="sys-visual-row" style={{ display: 'grid', gridTemplateColumns: '1fr 241.5px' }}>

                {/* Bar chart */}
                <div className="sys-visual-panel">
                  <div className="sys-visual-title" style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
                    Event activity - {sysSelectedDate
                        ? new Date(sysSelectedDate + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                        : `today (${new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })})`}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-end', gap: '3px', height: '56px' }}>
                    {(sysHourly.length > 0
                      ? sysHourly
                      : Array.from({ length: 24 }, (_, h) => ({ hour: h, Error: 0, Warning: 0, Success: 0, Info: 0 }))
                    ).map((h: any) => {
                      const total = (h.Error || 0) + (h.Warning || 0) + (h.Success || 0) + (h.Info || 0)
                      const maxVal = Math.max(...(sysHourly.length > 0 ? sysHourly : []).map((x: any) => (x.Error||0)+(x.Warning||0)+(x.Success||0)+(x.Info||0)), 1)
                      const heightPct = total > 0 ? Math.max(5, Math.round((total / maxVal) * 100)) : 3
                      const levelColors: Record<string, string> = { Error: '#d84343', Warning: '#dcbe5a', Success: '#7cca3c', Info: '#5599eb' }
                      const color = h.latestLevel ? (levelColors[h.latestLevel] || '#5599eb') : '#5599eb'
                      const tooltip = total > 0
                        ? `${h.hour}:00 - ${total} events\nError: ${h.Error||0}  Warning: ${h.Warning||0}  Success: ${h.Success||0}  Info: ${h.Info||0}`
                        : `${h.hour}:00 - no events`
                      return (
                        <div key={h.hour} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1, height: '100%', justifyContent: 'flex-end', gap: '3px' }}>
                          <div className={total === 0 ? 'sys-bar-empty' : ''} style={{ width: '100%', borderRadius: '3px 3px 0 0', background: total > 0 ? color : '#999d9d', height: `${heightPct}%`, minHeight: '2px' }} title={tooltip} />
                          <div className="sys-bar-label" style={{ fontSize: '10px' }}>{h.hour}</div>
                        </div>
                      )
                    })}
                  </div>
                  <div style={{ display: 'flex', gap: '11px', marginTop: '6px' }}>
                    {([['#d84343','Error'],['#dcbe5a','Warning'],['#7cca3c','Success'],['#5599eb','Info']] as [string,string][]).map(([color, label]) => (
                      <div key={label} className="sys-leg-label" style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10px' }}>
                        <div style={{ width: '8px', height: '8px', borderRadius: '2px', background: color }} />{label}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Donut chart */}
                <div className="sys-visual-panel">
                  <div className="sys-visual-title" style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
                    Event breakdown
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                    {(() => {
                      const total = sysSummary.total || 0
                      const circ  = 175.9
                      const s = (n: number) => total > 0 ? (n / total) * circ : 0
                      const suc = s(sysSummary.success)
                      const war = s(sysSummary.warnings)
                      const err = s(sysSummary.errors)
                      const inf = s(sysSummary.info)
                      return (
                        <svg width="80" height="80" viewBox="0 0 80 80">
                          <circle cx="40" cy="40" r="28" fill="none" stroke="#E5E7EB" strokeWidth="14"/>
                          {suc > 0 && <circle cx="40" cy="40" r="28" fill="none" stroke="#7cca3c" strokeWidth="14" strokeDasharray={`${suc} ${circ}`} strokeDashoffset="0" transform="rotate(-90 40 40)"/>}
                          {war > 0 && <circle cx="40" cy="40" r="28" fill="none" stroke="#dcbe5a" strokeWidth="14" strokeDasharray={`${war} ${circ}`} strokeDashoffset={`${-suc}`} transform="rotate(-90 40 40)"/>}
                          {err > 0 && <circle cx="40" cy="40" r="28" fill="none" stroke="#d84343" strokeWidth="14" strokeDasharray={`${err} ${circ}`} strokeDashoffset={`${-(suc+war)}`} transform="rotate(-90 40 40)"/>}
                          {inf > 0 && <circle cx="40" cy="40" r="28" fill="none" stroke="#5599eb" strokeWidth="14" strokeDasharray={`${inf} ${circ}`} strokeDashoffset={`${-(suc+war+err)}`} transform="rotate(-90 40 40)"/>}
                          <text className="sys-donut-count" x="40" y="37" textAnchor="middle" fontSize="14" fontWeight="500">{total}</text>
                          <text className="sys-donut-events" x="40" y="50" textAnchor="middle" fontSize="10">events</text>
                        </svg>
                      )
                    })()}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                      {([['#7cca3c','Success',sysSummary.success],['#5599eb','Info',sysSummary.info],['#dcbe5a','Warning',sysSummary.warnings],['#d84343','Error',sysSummary.errors]] as [string,string,number][]).map(([color, label, count]) => (
                        <div key={label} className="sys-donut-label" style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px' }}>
                          <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: color, flexShrink: 0 }} />
                          {label}
                          <span style={{ fontWeight: 700, marginLeft: 'auto', paddingLeft: '8px' }}>{count}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

              </div>
            )}

            {/* Summary stats */}
            {sysSummary && (
              <div className="sys-stats-row">
                {(() => {
                  const period = sysSelectedDate
                    ? new Date(sysSelectedDate + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
                    : 'All time'
                  return <>
                    <div className="sys-stat"><div className="sys-stat-dot" style={{ background: '#d84343' }}/><span className="sys-stat-label">Errors ({period})</span><span className="sys-stat-val" style={{ color: '#d84343' }}>{sysSummary.errors}</span></div>
                    <div className="sys-stat"><div className="sys-stat-dot" style={{ background: '#dcbe5a' }}/><span className="sys-stat-label">Warnings ({period})</span><span className="sys-stat-val" style={{ color: '#dcbe5a' }}>{sysSummary.warnings}</span></div>
                    <div className="sys-stat"><div className="sys-stat-dot" style={{ background: '#7cca3c' }}/><span className="sys-stat-label">Success ({period})</span><span className="sys-stat-val" style={{ color: '#7cca3c' }}>{sysSummary.success}</span></div>
                    <div className="sys-stat"><div className="sys-stat-dot" style={{ background: '#5599eb' }}/><span className="sys-stat-label">Info ({period})</span><span className="sys-stat-val" style={{ color: '#5599eb' }}>{sysSummary.info}</span></div>
                    <div className="sys-stat"><div className="sys-stat-dot" style={{ background: '#6B7280' }}/><span className="sys-stat-label">Total events ({period})</span><span className="sys-stat-val" style={{ color: '#6B7280' }}>{sysSummary.total}</span></div>
                  </>
                })()}
              </div>
            )}

            {/* Table */}
            {sysLoading ? (
              <div className="spinner-wrap">Loading operational logs...</div>
            ) : clientOffline ? (
              <div className="empty-state">No internet connection - server status unknown</div>
            ) : sysOffline ? (
              <div className="empty-state">Server offline</div>
            ) : sysLogs.length > 0 ? (
              <table className="admin-table sys-table">
                <colgroup>
                  <col style={{ width: '120px' }} />{/* Time */}
                  <col style={{ width: '100px' }} />{/* Level */}
                  <col style={{ width: '140px' }} />{/* Service */}
                  <col style={{ width: '590px' }} />{/* Event */}
                  <col style={{ width: '90px' }} />{/* Duration */}
                  <col style={{ width: '195px' }} />{/* Triggered by */}
                </colgroup>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Level</th>
                    <th>Service</th>
                    <th>Event</th>
                    <th>Duration</th>
                    <th>Triggered by</th>
                    <th>Job / Case</th>
                  </tr>
                </thead>
                <tbody>
                  {sysLogs.map((log, i) => (
                    <tr key={i}>
                      <td>
                        <div className="sys-date" style={{ fontSize: '13px', fontWeight: 470 }}>{log.time?.slice(0,10)}</div>
                        <div className="sys-time" style={{ fontSize: '12px', fontFamily: 'monospace',color: '#7e8287' }}>{log.time?.slice(11,19)}</div>
                      </td>
                      <td>
                        <span className={`sys-level ${
                          log.level === 'Error'   ? 'sys-error'   :
                          log.level === 'Warning' ? 'sys-warning' :
                          log.level === 'Success' ? 'sys-success' : 'sys-info'
                        }`}>{log.level}</span>
                      </td>
                      <td><span className="sys-service">{log.service}</span></td>
                      <td style={{ whiteSpace: 'normal' }}>
                        <div className="sys-msg" style={{ fontSize: '13px', lineHeight: 1.4 }}>{log.event}</div>
                        {log.detail && <div className="sys-detail" style={{ fontSize: '12px', color: '#72757a', marginTop: '2px', fontFamily: 'monospace', lineHeight: 1.4 }}>{log.detail}</div>}
                      </td>
                      <td>
                        {log.durationSeconds > 0 ? (
                          <div className="sys-dur" style={{ fontSize: '13px', fontWeight: 480, fontFamily: 'monospace' }}>
                            {log.durationSeconds >= 3600
                              ? `${Math.floor(log.durationSeconds/3600)}h ${Math.floor((log.durationSeconds%3600)/60)}m`
                              : log.durationSeconds >= 60
                              ? `${Math.floor(log.durationSeconds/60)}m ${Math.floor(log.durationSeconds%60)}s`
                              : `${log.durationSeconds}s`}
                          </div>
                        ) : <span className="sys-dash" style={{ fontSize: '13px' }}>-</span>}
                      </td>
                      <td>
                        {log.uploadedBy
                          ? <><div className="sys-user" style={{ fontSize: '13px', fontWeight: 480 }}>{log.uploadedBy}</div>
                              {log.court && <div className="sys-user-sub" style={{ fontSize: '12px', color: '#72757a' }}>{log.court}</div>}</>
                          : <span className="sys-sys" style={{ fontSize: '13.5px', fontStyle: 'italic', color: '#72757a' }}>System</span>}
                      </td>
                      <td>
                        {log.caseNumber
                          ? <><div className="sys-case" style={{ fontSize: '13px', fontWeight: 480 }}>{log.caseNumber}</div>
                              {log.court && <div className="sys-case-sub" style={{ fontSize: '12px', color: '#72757a' }}>{log.court}</div>}</>
                          : <span className="sys-dash" style={{ fontSize: '13px' }}>-</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

            ) : sysError ? (
              <div className="empty-state">{sysError}</div>
            ) : (
              <div className="empty-state">No log entries found.</div>
            )}
          </div>
        )}

      </main>

      {/* ── Footer ── */}
      
        <Footer />
      

      {/* ════ MODALS ════ */}
      {modal && (
        <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) closeModal() }}>
          <div className={modal === 'create' ? 'modal-box-lg' : 'modal-box'}>

            {/* ── Create user modal ── */}
            {modal === 'create' && (
              <>
                {/* ── Modal header ── */}
                <div className="modal-header">
                  <div className="modal-header-icon">
                    <svg width="18" height="18" viewBox="0 0 24 24"
                        fill="none" stroke="#185FA5" strokeWidth="2">
                      <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/>
                      <circle cx="12" cy="7" r="4"/>
                    </svg>
                  </div>
                  <div>
                    <div className="modal-header-title">Create new user account</div>
                    <div className="modal-header-sub">
                      Add a new staff member to the system
                    </div>
                  </div>
                </div>

                {formError && <div className="form-error">{formError}</div>}

                {/* ── Personal info ── */}
                <div className="modal-section-title">Personal information</div>
                <div className="form-grid-2">
                  <div className="form-field">
                    <label>Full name</label>
                    <input
                      className="form-input"
                      placeholder="e.g. Kamal Perera"
                      value={form.displayName}
                      onChange={e => setForm(f => ({ ...f, displayName: e.target.value }))}
                    />
                  </div>
                  <div className="form-field">
                    <label>Email address</label>
                    <input
                      className="form-input"
                      type="email"
                      placeholder="e.g. user@courts.lk"
                      value={form.email}
                      onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                    />
                  </div>
                </div>

                {/* ── Account credentials ── */}
                <div className="modal-section-title">Account credentials</div>
                <div className="form-grid-2">
                  <div className="form-field">
                    <label>Username</label>
                    <input
                      className="form-input"
                      placeholder="e.g. court1staff"
                      value={form.username}
                      onChange={e => setForm(f => ({
                        ...f,
                        username: e.target.value.toLowerCase().replace(/\s/g, '')
                      }))}
                    />
                    <div className="form-input-hint">Lowercase only, no spaces</div>
                  </div>
                  <div className="form-field">
                    <label>Password</label>
                    <div className="pw-input-wrap">
                      <input
                        className="form-input"
                        type={showPw ? 'text' : 'password'}
                        placeholder="Min. 6 characters"
                        value={form.password}
                        onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                      />
                      <button
                        type="button"
                        title={showPw ? 'Hide' : 'Show'}
                        aria-label={showPw ? 'Hide password' : 'Show password'}
                        className="pw-toggle-btn"
                        onClick={() => setShowPw(s => !s)}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24"
                            fill="none" stroke="currentColor" strokeWidth="1.8">
                          {showPw
                            ? <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24M1 1l22 22"/>
                            : <><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></>
                          }
                        </svg>
                      </button>
                    </div>
                    <div className="form-input-hint">Minimum 6 characters</div>
                  </div>
                </div>

                {/* ── Role & court ── */}
                <div className="modal-section-title">Role & court assignment</div>
                <div className="form-grid-2">
                  <div className="form-field">
                    <label htmlFor="role">Role</label>
                    <select
                      id="role"
                      className="form-select"
                      value={form.role}
                      onChange={e => setForm(f => ({
                        ...f,
                        role:  e.target.value as CreateUserData['role'],
                        court: e.target.value === 'System Administrator' ? '' : courts[0]?.name || '',
                      }))}
                    >
                      <option value="Staff User">Staff User</option>
                      <option value="Judge">Judge</option>
                      <option value="System Administrator">System Administrator</option>
                    </select>
                  </div>
                  {(form.role === 'Staff User' || form.role === 'Judge') && (
                    <div className="form-field">
                      <label htmlFor="court">Court</label>
                      <select
                        id="court"
                        className="form-select"
                        value={form.court}
                        onChange={e => setForm(f => ({ ...f, court: e.target.value }))}
                      >
                        {courts.map(court => (
                          <option key={court.id} value={court.name}>{court.name}</option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>

                {/* ── Verification note ── */}
                <div className="modal-info-note">
                  <svg width="14" height="14" viewBox="0 0 24 24"
                      fill="none" stroke="currentColor" strokeWidth="2"
                      style={{ flexShrink: 0, marginTop: '1px' }}>
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="12" y1="8" x2="12" y2="12"/>
                    <line x1="12" y1="16" x2="12.01" y2="16"/>
                  </svg>
                  A verification email will be sent to the user automatically.
                  The user must verify their email before they can log in.
                </div>

                <div className="modal-actions">
                  <button type="button" className="btn-secondary"
                    onClick={closeModal} disabled={formLoading}>
                    Cancel
                  </button>
                  <button type="button" className="btn-primary"
                    onClick={handleCreateUser} disabled={formLoading}>
                    {formLoading ? 'Creating...' : 'Create user account'}
                  </button>
                </div>
              </>
            )}

            {/* ── Change password modal ── */}
            {modal === 'password' && selectedUser && (
              <>
                <div className="modal-title">Change Court Assignment</div>

                {/* User info card */}
                <div className="modal-user-card">
                  <div className="modal-user-avatar">{initials(selectedUser.displayName)}</div>
                  <div>
                    <div className="modal-user-name">{selectedUser.displayName}</div>
                    <div className="modal-user-meta">{selectedUser.username} &nbsp;·&nbsp; {selectedUser.email}</div>
                  </div>
                </div>

                {selectedUser.role === 'System Administrator' ? (
                  <div className="modal-info-banner">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                        stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10"/>
                      <line x1="12" y1="8" x2="12" y2="12"/>
                      <line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                    System Administrators are not assigned to a specific court.
                  </div>
                ) : (
                  <>
                    <div className="modal-current-value">
                      <span className="modal-current-label">Current court</span>
                      <span className="modal-current-text">{selectedUser.court || '-'}</span>
                    </div>
                    <div className="form-field">
                      <label>Assign to new court</label>
                      <select
                        className="form-select"
                        value={editCourt}
                        onChange={e => setEditCourt(e.target.value)}
                      >
                        {courts.map(court => (
                          <option key={court.id} value={court.name}>{court.name}</option>
                        ))}
                      </select>
                    </div>
                  </>
                )}

                <div className="modal-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={closeModal}
                    disabled={editLoading}
                  >
                    Cancel
                  </button>
                  {selectedUser.role !== 'System Administrator' && (
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={handleEditUser}
                      disabled={editLoading}
                    >
                      {editLoading ? 'Saving...' : 'Save changes'}
                    </button>
                  )}
                </div>
              </>
            )}

            {/* ── Delete user modal ── */}
            {modal === 'delete' && selectedUser && (
              <>
                <div className="modal-title">Delete User Account</div>

                {/* User info card */}
                <div className="modal-user-card">
                  <div className="modal-user-avatar">{initials(selectedUser.displayName)}</div>
                  <div>
                    <div className="modal-user-name">{selectedUser.displayName}</div>
                    <div className="modal-user-meta">{selectedUser.username} &nbsp;·&nbsp; {selectedUser.court || 'No court'}</div>
                  </div>
                </div>

                {/* What gets deleted */}
                <div className="modal-delete-checklist">
                  <div className="modal-delete-checklist-title">The following will be removed:</div>
                  <div className="modal-delete-item">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                        stroke="#A32D2D" strokeWidth="2">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    User profile and court assignment
                  </div>
                  <div className="modal-delete-item">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                        stroke="#A32D2D" strokeWidth="2">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    Access to the system
                  </div>
                </div>

                <div className="modal-danger-banner">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2">
                    <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
                    <line x1="12" y1="9" x2="12" y2="13"/>
                    <line x1="12" y1="17" x2="12.01" y2="17"/>
                  </svg>
                  This action is permanent and cannot be undone.
                </div>

                <div className="modal-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={closeModal}
                    disabled={formLoading}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn-danger-lg"
                    onClick={handleDeleteUser}
                    disabled={formLoading}
                  >
                    {formLoading ? 'Deleting...' : 'Delete user'}
                  </button>
                </div>
              </>
            )}

          </div>
        </div>
      )}

      {/* ── Toast ── */}
      {toast && (
        <div className={`admin-toast ${toast.type === 'error' ? 'error' : ''}`}>
          {toast.msg}
        </div>
      )}

    </div>
  )
}
