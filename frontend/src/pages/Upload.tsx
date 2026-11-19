import { useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { doc, updateDoc, setDoc, deleteDoc, getDoc } from 'firebase/firestore'
import { updatePassword,
         updateEmail,
         EmailAuthProvider,
         reauthenticateWithCredential, } from 'firebase/auth'
import { auth, db } from '../firebase/config'
import { useAuth } from '../context/AuthContext'
import Header from '../components/shared/Header'
import Footer from '../components/shared/Footer'
import {
  downloadTranscript,
  fetchTranscriptAudioUrl,
  finalizeTranscript,
  generateVerifiedOutputs,
  getTranscript,
  getUploadJob,
  listTranscriptDates,
  listTranscripts,
  saveEditedTranscript,
  uploadAudio,
  getFilePresignedUrl,
} from '../api/transcriptionApi'
import type { TranscriptItem, TranscriptFile, UploadJob } from '../api/transcriptionApi'
import { displayError } from '../api/systemErrors'
import CourtDatePicker from '../components/shared/CourtDatePicker'
import './Upload.css'

type Tab = 'upload' | 'files' | 'profile'
type TranscriptView = 'transcript' | 'proceeding' | 'translations'
type KeyboardMode = 'english' | 'wij9' | 'helakuru'

const SUPPORTED = ['.flac', '.mp3', '.wav']
const MAX_SIZE = 2 * 1024 * 1024 * 1024

const TRANSLATION_CHOICES = [
  { code: 'si', label: 'Sinhala' },
  { code: 'ta', label: 'Tamil' },
  { code: 'en', label: 'English' },
]

const UPLOAD_PROCESSING_MESSAGES = [
  'Uploading to server...'
]

const TERMINAL_JOB_STATUSES = new Set<UploadJob['status']>(['completed', 'failed'])
const FAST_UPLOAD_STATUSES = new Set<UploadJob['status']>([
  'queued',
  'uploaded_to_backend',
  'saving_to_s3',
  'uploaded_to_s3',
  'preparing_audio',
  'enhancing_audio',
  'chunking_audio',
  'uploading_to_gcs',
  'uploading_chunks_to_gcs',
  'submitted_to_vertex',
  'downloading_results',
  'repairing_transcript',
  'merging_transcript',
  'saving_transcript_to_s3',
  'verifying',
  'saving',
])

const WIJ9_NORMAL: Record<string, string> = {
  Backquote: '්‍ර', Digit1: '1', Digit2: '2', Digit3: '3', Digit4: '4', Digit5: '5', Digit6: '6', Digit7: '7', Digit8: '8', Digit9: '9', Digit0: '0', Minus: '-', Equal: '=', Backslash: '්',
  KeyQ: 'ු', KeyW: 'අ', KeyE: 'ැ', KeyR: 'ර', KeyT: 'එ', KeyY: 'හ', KeyU: 'ම', KeyI: 'ස', KeyO: 'ද', KeyP: 'ච', BracketLeft: 'ඤ', BracketRight: ';',
  KeyA: '්', KeyS: 'ි', KeyD: 'ා', KeyF: 'ෙ', KeyG: 'ට', KeyH: 'ය', KeyJ: 'ව', KeyK: 'න', KeyL: 'ක', Semicolon: 'ත', Quote: '.',
  KeyZ: '‘', KeyX: 'ං', KeyC: 'ජ', KeyV: 'ඩ', KeyB: 'ඉ', KeyN: 'බ', KeyM: 'ප', Comma: 'ල', Period: 'ග', Slash: '/',
}

const WIJ9_SHIFT: Record<string, string> = {
  Backquote: 'ර්‍', Digit1: '!', Digit2: '@', Digit3: '#', Digit4: '$', Digit5: '%', Digit6: '^', Digit7: '&', Digit8: '*', Digit9: '(', Digit0: ')', Minus: '_', Equal: '+',
  KeyQ: 'ූ', KeyW: 'උ', KeyE: 'ෑ', KeyR: 'ඍ', KeyT: 'ඔ', KeyY: 'ශ', KeyU: 'ඹ', KeyI: 'ෂ', KeyO: 'ධ', KeyP: 'ඡ', BracketLeft: 'ඥ', BracketRight: ':',
  KeyA: 'ෟ', KeyS: 'ී', KeyD: 'ෘ', KeyF: 'ෆ', KeyG: 'ඨ', KeyH: '්‍ය', KeyJ: 'ළු', KeyK: 'ණ', KeyL: 'ඛ', Semicolon: 'ථ', Quote: ',',
  KeyZ: '“', KeyX: 'ඃ', KeyC: 'ඣ', KeyV: 'ඪ', KeyB: 'ඊ', KeyN: 'භ', KeyM: 'ඵ', Comma: 'ළ', Period: 'ඝ', Slash: '?',
}

const WIJ9_ALT: Record<string, string> = {
  Backquote: '`', Digit1: '¡', Digit2: '²', Digit3: '³', Digit4: '¤', Digit5: '€', Digit6: '¼', Digit7: '½', Digit8: '¾', Digit9: '‘', Digit0: '’', Minus: '¥', Equal: '×', Backslash: '\\',
  KeyQ: 'ෞ', KeyW: 'ො', KeyE: 'ෛ', KeyR: 'ෙ', KeyT: 'ේ', KeyY: 'ඒ', KeyU: 'ඎ', KeyO: 'ඳ', BracketLeft: '[', BracketRight: ']',
  KeyA: 'ෳ', KeyS: 'ෝ', KeyD: 'ෲ', KeyF: 'ආ', KeyG: 'ඇ', KeyH: 'ඈ', KeyK: 'ඓ', KeyL: 'ඕ', Semicolon: 'ඖ', Quote: '෴',
  KeyZ: 'ඌ', KeyX: 'ඞ', KeyC: 'ඦ', KeyV: 'ඬ', KeyB: 'ඐ', Period: 'ඏ', Slash: 'ඟ',
}

const WIJ9_ALT_SHIFT: Record<string, string> = {
  Backquote: '~', Digit1: '¹', Digit3: '£', Equal: '÷', Backslash: '|',
  BracketLeft: '{', BracketRight: '}', Comma: '<', Period: '>', Slash: '¿',
}

const HELAKURU_CONSONANTS = ([
  ['chha', 'ඡ'], ['thh', 'ථ'], ['dhh', 'ධ'], ['Sha', 'ෂ'], ['zga', 'ඟ'], ['zja', 'ඦ'], ['zdha', 'ඳ'], ['zqa', 'ඳ'], ['zka', 'ඤ'], ['zha', 'ඥ'], ['zda', 'ඬ'],
  ['kha', 'ඛ'], ['gha', 'ඝ'], ['cha', 'ච'], ['Ta', 'ඨ'], ['Da', 'ඪ'], ['pha', 'ඵ'], ['bha', 'භ'], ['sha', 'ශ'],
  ['tha', 'ත'], ['dha', 'ද'], ['qa', 'ද'], ['Na', 'ණ'], ['La', 'ළ'], ['Sa', 'ෂ'], ['Ba', 'ඹ'], ['Lu', 'ළු'],
  ['ka', 'ක'], ['ga', 'ග'], ['ja', 'ජ'], ['ta', 'ට'], ['da', 'ඩ'], ['na', 'න'], ['pa', 'ප'], ['ba', 'බ'], ['ma', 'ම'], ['ya', 'ය'], ['ra', 'ර'], ['la', 'ල'], ['wa', 'ව'], ['va', 'ව'], ['sa', 'ස'], ['ha', 'හ'], ['fa', 'ෆ'],
  ['k', 'ක'], ['g', 'ග'], ['j', 'ජ'], ['t', 'ට'], ['d', 'ඩ'], ['n', 'න'], ['N', 'ණ'], ['p', 'ප'], ['b', 'බ'], ['m', 'ම'], ['y', 'ය'], ['r', 'ර'], ['l', 'ල'], ['L', 'ළ'], ['w', 'ව'], ['v', 'ව'], ['s', 'ස'], ['S', 'ෂ'], ['h', 'හ'], ['f', 'ෆ'],
 ] as Array<[string, string]>).sort((a, b) => b[0].length - a[0].length)

const HELAKURU_VOWELS = ([
  ['Aa', 'ඈ'], ['AA', 'ඈ'], ['aa', 'ආ'], ['ii', 'ඊ'], ['uu', 'ඌ'], ['Ru', 'ඎ'], ['ee', 'ඒ'], ['ai', 'ඓ'], ['oo', 'ඕ'], ['au', 'ඖ'], ['ou', 'ඖ'],
  ['a', 'අ'], ['A', 'ඇ'], ['i', 'ඉ'], ['u', 'උ'], ['R', 'ඍ'], ['e', 'එ'], ['o', 'ඔ'],
 ] as Array<[string, string]>).sort((a, b) => b[0].length - a[0].length)

const HELAKURU_SIGNS = ([
  ['Aa', 'ෑ'], ['AA', 'ෑ'], ['aa', 'ා'], ['ii', 'ී'], ['uu', 'ූ'], ['ee', 'ේ'], ['ai', 'ෛ'], ['oo', 'ෝ'], ['au', 'ෞ'], ['ou', 'ෞ'],
  ['A', 'ැ'], ['i', 'ි'], ['u', 'ු'], ['e', 'ෙ'], ['o', 'ො'], ['a', ''],
 ] as Array<[string, string]>).sort((a, b) => b[0].length - a[0].length)

const HELAKURU_STANDALONE: Record<string, string> = { H: 'ඃ', x: 'ං', zn: 'ං', X: 'ඞ' }

function helakuruToSinhalaToken(input: string) {
  let out = ''
  let i = 0

  while (i < input.length) {
    const rest = input.slice(i)
    const standalone = Object.entries(HELAKURU_STANDALONE).find(([key]) => rest.startsWith(key))
    if (standalone) {
      out += standalone[1]
      i += standalone[0].length
      continue
    }

    const vowel = HELAKURU_VOWELS.find(([key]) => rest.startsWith(key))
    if (vowel) {
      out += vowel[1]
      i += vowel[0].length
      continue
    }

    const consonant = HELAKURU_CONSONANTS.find(([key]) => rest.startsWith(key))
    if (!consonant) {
      out += input[i]
      i += 1
      continue
    }

    const [key, letter] = consonant
    i += key.length
    const after = input.slice(i)

    if (after.startsWith('ruu')) {
      out += letter + 'ෲ'
      i += 3
      continue
    }
    if (after.startsWith('ru')) {
      out += letter + 'ෘ'
      i += 2
      continue
    }
    if (after.startsWith('ya')) {
      out += letter + '්‍ය'
      i += 2
      continue
    }
    if (after.startsWith('ra')) {
      out += letter + '්‍ර'
      i += 2
      continue
    }

    const sign = HELAKURU_SIGNS.find(([vowelKey]) => after.startsWith(vowelKey))
    if (sign) {
      out += letter + sign[1]
      i += sign[0].length
    } else {
      out += letter + '්'
    }
  }

  return out || input
}

function pad(n: number) { return String(n).padStart(2, '0') }

function getNow() {
  const d = new Date()
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  const time = `${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`
  return { date, time }
}

function formatSize(bytes: number) {
  if (bytes > 1073741824) return (bytes / 1073741824).toFixed(1) + ' GB'
  if (bytes > 1048576) return Math.round(bytes / 1048576) + ' MB'
  return Math.max(1, Math.round(bytes / 1024)) + ' KB'
}

function fileBaseName(path: string) {
  const clean = (path || '').split(/[?#]/, 1)[0]
  return clean.replace(/\\/g, '/').split('/').filter(Boolean).pop() || clean
}

function buildCourtStoragePath(transcript: TranscriptItem | undefined, fallback: {
  court: string
  date: string
  caseNumber: string
  fileName: string
  sourcePath: string
}) {
  if (!transcript) {
    return [fallback.court, fallback.date, fallback.caseNumber, fallback.fileName].filter(Boolean).join('/') || fallback.sourcePath || 'Court files'
  }

  return [
    transcript.court || fallback.court,
    transcript.date || fallback.date,
    transcript.caseNumber || fallback.caseNumber,
    transcript.audioFileName || fallback.fileName,
  ].filter(Boolean).join('/')
}

function progressForStatus(status: UploadJob['status']) {
  if (status === 'uploaded_to_backend') return 18
  if (status === 'uploading') return 25
  if (status === 'saving_to_s3') return 25
  if (status === 'uploaded_to_s3') return 32
  if (status === 'preparing_audio') return 38
  if (status === 'enhancing_audio') return 44
  if (status === 'uploading_to_gcs') return 58
  if (status === 'chunking_audio') return 50
  if (status === 'uploading_chunks_to_gcs') return 58
  if (status === 'submitted_to_vertex') return 64
  if (status === 'submitted') return 68
  if (status === 'processing') return 74
  if (status === 'downloading_results') return 84
  if (status === 'repairing_transcript') return 88
  if (status === 'verifying') return 86
  if (status === 'merging_transcript') return 90
  if (status === 'saving_transcript_to_s3') return 94
  if (status === 'post_processing') return 78
  if (status === 'saving') return 92
  if (status === 'completed') return 100
  return 0
}

function boundedJobProgress(status: UploadJob['status'], current: number) {
  const next = progressForStatus(status)
  if (status === 'completed') return 100
  if (status === 'failed') return 0
  return Math.min(99, Math.max(current, next))
}

function uploadPollDelay(status: UploadJob['status']) {
  if (FAST_UPLOAD_STATUSES.has(status)) return 1500
  if (status === 'submitted' || status === 'processing' || status === 'transcribing') return 3000
  return 2000
}

function jobErrorMessage(job: UploadJob, fallback: string) {
  const message = job.error?.message || job.message || fallback
  const code = job.error?.code || job.errorCode
  return code ? `${message} (Error code: ${code})` : message
}

function mergeTranscriptSummaries(current: TranscriptItem[], incoming: TranscriptItem[]) {
  return incoming.map(next => {
    const existing = current.find(item => item.id === next.id)
    if (!existing?.contentLoaded || next.contentLoaded) return next

    return {
      ...next,
      contentLoaded: true,
      transcriptText: existing.transcriptText,
      editedTranscriptText: existing.editedTranscriptText,
      finalizedTranscriptText: existing.finalizedTranscriptText,
      summaryText: existing.summaryText,
      translations: existing.translations,
      srtText: existing.srtText,
    }
  })
}

function getWij9Char(e: KeyboardEvent<HTMLTextAreaElement>) {
  const altGraph = e.getModifierState('AltGraph') || (e.altKey && e.ctrlKey)
  if (e.metaKey || (e.ctrlKey && !altGraph)) return null
  if (e.key.length !== 1) return null

  const map = altGraph
    ? (e.shiftKey ? WIJ9_ALT_SHIFT : WIJ9_ALT)
    : (e.shiftKey ? WIJ9_SHIFT : WIJ9_NORMAL)
  return map[e.code] ?? null
}

interface UploadSuccess {
  caseNumber: string
  date: string
  time: string
  fileName: string
  fileNames: string[]
  path: string
  size: string
}
interface TimedTranscriptLine {
  id: string
  text: string
  start: number | null
  end: number | null
  timeLabel: string
}

const TIME_TOKEN_PATTERN = String.raw`(?:\d{1,2}:\d{2}(?::\d{2})?(?:[\.,]\d{1,3})?|\d+\s*h\s*\d+\s*m\s*\d+(?:[\.,]\d+)?\s*s(?:\s*\d+\s*ms)?|\d+\s*m\s*\d+(?:[\.,]\d+)?\s*s(?:\s*\d+\s*ms)?|\d+(?:[\.,]\d+)?\s*s(?:\s*\d+\s*ms)?|\d+\s*ms)`
const TRANSCRIPT_RANGE_RE = new RegExp(
  String.raw`^\s*\[?\s*(${TIME_TOKEN_PATTERN})\s*(?:-->|-|\u2013|\u2014|to)\s*(${TIME_TOKEN_PATTERN})\s*\]?\s*(.*)$`,
  'i',
)

function parseTimeToken(raw: string) {
  const value = raw.trim().toLowerCase().replace(/,/g, '.')
  if (!value) return null

  if (!value.includes(':') && /[hms]/.test(value)) {
    let total = 0
    const hours = value.match(/(\d+(?:\.\d+)?)\s*h/)
    const minutes = value.match(/(\d+(?:\.\d+)?)\s*m(?!s)/)
    const seconds = value.match(/(\d+(?:\.\d+)?)\s*s/)
    const millis = value.match(/(\d+(?:\.\d+)?)\s*ms/)

    if (hours) total += Number(hours[1]) * 3600
    if (minutes) total += Number(minutes[1]) * 60
    if (seconds) total += Number(seconds[1])
    if (millis) total += Number(millis[1]) / 1000

    return Number.isFinite(total) ? total : null
  }

  const parts = value.split(':')
  if (parts.length === 3) {
    const [hours, minutes, seconds] = parts.map(Number)
    const total = hours * 3600 + minutes * 60 + seconds
    return Number.isFinite(total) ? total : null
  }
  if (parts.length === 2) {
    const [minutes, seconds] = parts.map(Number)
    const total = minutes * 60 + seconds
    return Number.isFinite(total) ? total : null
  }

  const seconds = Number(value)
  return Number.isFinite(seconds) ? seconds : null
}

function parseTranscriptLines(text: string): TimedTranscriptLine[] {
  return text.split(/\r?\n/).map((raw, index) => {
    const match = raw.match(TRANSCRIPT_RANGE_RE)
    if (!match) {
      return { id: String(index), text: raw, start: null, end: null, timeLabel: '' }
    }

    const start = parseTimeToken(match[1])
    const end = parseTimeToken(match[2])
    if (start === null || end === null) {
      return { id: String(index), text: raw, start: null, end: null, timeLabel: '' }
    }

    return {
      id: String(index),
      text: match[3].trim(),
      start,
      end,
      timeLabel: `${formatPlaybackTime(start)} - ${formatPlaybackTime(end)}`,
    }
  })
}

function formatPlaybackTime(seconds: number) {
  const bounded = Math.max(0, seconds)
  const totalSeconds = Math.floor(bounded)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const secs = totalSeconds % 60

  if (hours > 0) return `${hours}:${pad(minutes)}:${pad(secs)}`
  return `${minutes}:${pad(secs)}`

}

export default function Upload() {
  const { user, refreshUser } = useAuth()

  const [activeTab, setActiveTab] = useState<Tab>('upload')
  const [caseNumber, setCaseNumber] = useState('')
  const [downloadingVersion, setDownloadingVersion] = useState<string | null>(null)
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [customNames, setCustomNames] = useState<string[]>([])
  const [dragActive, setDragActive] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [processingMessageIndex, setProcessingMessageIndex] = useState(0)
  const [uploadSuccess, setUploadSuccess] = useState<UploadSuccess | null>(null)
  const [uploadJob, setUploadJob] = useState<UploadJob | null>(null)
  const [translationLanguages, setTranslationLanguages] = useState<string[]>([])
  const [includeSummary, setIncludeSummary] = useState(false)
  const [finalizing, setFinalizing] = useState(false)
  const [postProcessing, setPostProcessing] = useState(false)
  const [postProcessJob, setPostProcessJob] = useState<UploadJob | null>(null)
  const [postProcessProgress, setPostProcessProgress] = useState(0)
  const [postProcessError, setPostProcessError] = useState('')
  const [postProcessBackground, setPostProcessBackground] = useState(false)
  const [postProcessItemId, setPostProcessItemId] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement>(null)
  const transcriptLineRefs = useRef<Record<number, HTMLDivElement | null>>({})
  const fileInputRef = useRef<HTMLInputElement>(null)
  const submittedToastShown = useRef(false)
  const [hearingDate, setHearingDate] = useState<string>(getNow().date)
  const [showCalendar, setShowCalendar] = useState(false)
  const [currentTime, setCurrentTime] = useState(getNow().time)

  const [filesDate, setFilesDate] = useState<string>(getNow().date)
  const [availableFileDates, setAvailableFileDates] = useState<string[]>([])
  const [fileDatesLoaded, setFileDatesLoaded] = useState(false)
  const [fileDatesLoading, setFileDatesLoading] = useState(false)
  const [transcripts, setTranscripts] = useState<TranscriptItem[]>([])
  const [selectedTranscriptId, setSelectedTranscriptId] = useState('')
  const [filesLoading, setFilesLoading] = useState(false)
  const [filesError, setFilesError] = useState('')
  const [transcriptDetailLoading, setTranscriptDetailLoading] = useState(false)
  const [transcriptView, setTranscriptView] = useState<TranscriptView>('transcript')
  const [isEditing, setIsEditing] = useState(false)
  const [editedText, setEditedText] = useState('')
  const [audioUrls, setAudioUrls] = useState<{ name: string; url: string }[]>([])
  const [audioLoadRequested, setAudioLoadRequested] = useState(false)
  const [audioCurrentTime, setAudioCurrentTime] = useState(0)
  const [editSaving, setEditSaving] = useState(false)
  const [keyboardMode, setKeyboardMode] = useState<KeyboardMode>('english')
  const [uploadComplete, setUploadComplete] = useState(false)
  const pendingSuccessRef = useRef<UploadSuccess | null>(null)
  const [profileForm, setProfileForm] = useState({
    displayName: user?.displayName ?? '',
    username: user?.username ?? '',
    email: user?.email ?? '',
    currentPw: '',
    newPw: '',
    confirmPw: '',
  })
  const [profileError, setProfileError] = useState('')
  const [profileSuccess, setProfileSuccess] = useState('')
  const [profileLoading, setProfileLoading] = useState(false)
  const [showCurrPw, setShowCurrPw] = useState(false)
  const [showNewPw, setShowNewPw] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)
  const activeUploadJob = uploadJob && !TERMINAL_JOB_STATUSES.has(uploadJob.status) ? uploadJob : null
 const uploadProgressMessage = progress === 100
    ? 'Upload complete!'
    : activeUploadJob?.message || UPLOAD_PROCESSING_MESSAGES[processingMessageIndex]
  const visibleUploadProgress = Math.round(Math.min(progress, 100))

  const selectedTranscript = useMemo(
    () => transcripts.find(item => item.id === selectedTranscriptId) ?? transcripts[0] ?? null,
    [selectedTranscriptId, transcripts],
  )
  const selectedTranscriptDetails = useMemo(() => {
    if (!selectedTranscript) return []

    return [
      { label: 'Case number', value: selectedTranscript.caseNumber || selectedTranscript.folderName },
      { label: 'Hearing date', value: selectedTranscript.date },
      { label: 'Uploaded by', value: selectedTranscript.uploadedBy },
    ].filter(row => row.value)
  }, [selectedTranscript, user?.court])

  const transcriptDisplayText = selectedTranscript?.finalizedTranscriptText || selectedTranscript?.editedTranscriptText || selectedTranscript?.transcriptText || ''
  const transcriptLines = useMemo(() => parseTranscriptLines(transcriptDisplayText), [transcriptDisplayText])
  const hasTimedTranscript = useMemo(
    () => transcriptLines.some(line => line.start !== null && line.end !== null),
    [transcriptLines],
  )
  const activeTranscriptLineIndex = useMemo(() => {
    if (!hasTimedTranscript || isEditing || transcriptView !== 'transcript') return -1
    return transcriptLines.findIndex(line => {
      if (line.start === null || line.end === null) return false
      return audioCurrentTime >= line.start && audioCurrentTime < Math.max(line.end, line.start + 0.15)
    })
  }, [audioCurrentTime, hasTimedTranscript, isEditing, transcriptLines, transcriptView])

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(getNow().time), 1000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!uploading) {
      setProcessingMessageIndex(0)
      return
    }

    const timer = setInterval(() => {
      setProcessingMessageIndex(index => (index + 1) % UPLOAD_PROCESSING_MESSAGES.length)
    }, 2400)

    return () => clearInterval(timer)
  }, [uploading])

  useEffect(() => {
    if (!uploading) return

    const timer = setInterval(() => {
      setProgress(current => {
        if (current >= 100) return 100

        if (uploadComplete) {
          const step = current < 50 ? 2.0
                    : current < 70 ? 1.5
                    : current < 85 ? 1.0
                    : current < 95 ? 0.5
                    : 0.2
          return Math.min(100, current + step)
        }

        // Creep to 22 while waiting for backend
        if (current >= 22) return current
        return current + 0.4
      })
    }, 120)

    return () => clearInterval(timer)
  }, [uploading, uploadComplete])

  useEffect(() => {
    if (progress < 100 || !pendingSuccessRef.current) return
    const successData = pendingSuccessRef.current
    pendingSuccessRef.current = null
    if (!submittedToastShown.current) {
      submittedToastShown.current = true
      showToast('Audio uploaded - transcription starting')
    }
    const timer = setTimeout(() => {
      setUploading(false)
      setUploadComplete(false)
      setUploadSuccess(successData)
    }, 400)
    return () => clearTimeout(timer)
  }, [progress])

  useEffect(() => {
    if (!user) return
    setProfileForm(form => ({
      ...form,
      displayName: user.displayName ?? '',
      username: user.username ?? '',
      email: user.email ?? '',
    }))
  }, [user])

  useEffect(() => {
    if (!user) return

    let cancelled = false
    async function loadDates() {
      setFileDatesLoading(true)
      setFileDatesLoaded(false)
      try {
        const data = await listTranscriptDates()
        if (cancelled) return
        const dates = data.dates ?? []
        setAvailableFileDates(dates)
        setFilesDate(current => {
          if (current && dates.includes(current)) return current
          return dates[0] ?? data.defaultDate ?? getNow().date
        })
      } catch {
        if (!cancelled) {
          setAvailableFileDates([])
        }
      } finally {
        if (!cancelled) {
          setFileDatesLoaded(true)
          setFileDatesLoading(false)
        }
      }
    }

    void loadDates()
    return () => { cancelled = true }
  }, [user])

  useEffect(() => {
    if (!user || !fileDatesLoaded) return

    let cancelled = false
    async function load() {
      setFilesLoading(true)
      setFilesError('')
      try {
        const items = await listTranscripts(filesDate)
        if (cancelled) return
        setTranscripts(current => mergeTranscriptSummaries(current, items))
        setSelectedTranscriptId(current => {
          if (items.some(item => item.id === current)) return current
          return items[0]?.id ?? ''
        })
      } catch (err) {
        if (!cancelled) {
          setFilesError(displayError(err))
          setTranscripts([])
          setSelectedTranscriptId('')
        }
      } finally {
        if (!cancelled) setFilesLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [user,fileDatesLoaded, filesDate])

  useEffect(() => {
    if (activeTab !== 'files' || !fileDatesLoaded) return

    let cancelled = false

    async function refresh() {
      if (cancelled) return
      try {
        const items = await listTranscripts(filesDate)
        if (cancelled) return
        setTranscripts(current => mergeTranscriptSummaries(current, items))
        setSelectedTranscriptId(current => {
          if (items.some(item => item.id === current)) return current
          return items[0]?.id ?? ''
        })
      } catch {
        // silently ignore poll errors
      }
    }

    void refresh()                    

    const timer = setInterval(refresh, 30000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [activeTab, fileDatesLoaded, filesDate])

  useEffect(() => {
    if (!selectedTranscript || selectedTranscript.contentLoaded) return

    let cancelled = false
    async function loadTranscriptDetail() {
      setTranscriptDetailLoading(true)
      try {
        const detail = await getTranscript(selectedTranscript.id)
        if (cancelled) return
        setTranscripts(items => items.map(item => item.id === detail.id ? detail : item))
      } catch (err) {
        if (!cancelled) showToast(displayError(err), 'error')
      } finally {
        if (!cancelled) setTranscriptDetailLoading(false)
      }
    }

    void loadTranscriptDetail()
    return () => { cancelled = true }
  }, [selectedTranscript?.id, selectedTranscript?.contentLoaded])

  // Effect 1 — full reset only when transcript ID changes (switching files)
  useEffect(() => {
    setIsEditing(false)
    setAudioCurrentTime(0)
    setAudioUrls([])
    setAudioLoadRequested(true)
    transcriptLineRefs.current = {}
    setTranscriptView('transcript')
    setEditedText('')
  }, [selectedTranscript?.id])

  // Effect 2 — only update text when content loads, never touch audio
  useEffect(() => {
    if (!selectedTranscript?.contentLoaded) return
    setEditedText(
      selectedTranscript.finalizedTranscriptText ||
      selectedTranscript.editedTranscriptText ||
      selectedTranscript.transcriptText
    )
  }, [selectedTranscript?.id, selectedTranscript?.contentLoaded])

  useEffect(() => {
    if (activeTranscriptLineIndex < 0) return
    transcriptLineRefs.current[activeTranscriptLineIndex]?.scrollIntoView({
      block: 'center',
      behavior: 'smooth',
    })
  }, [activeTranscriptLineIndex])

  useEffect(() => {
    if (!selectedTranscript?.audioAvailable || !audioLoadRequested) {
      setAudioUrls([])
      return
    }

    let cancelled = false
    const objectUrls: string[] = []

    async function loadAudios() {
      try {
        const AUDIO_EXTS = ['.flac', '.mp3', '.wav',]

        // Use audioFiles from backend if available, otherwise filter from files
        const audioFiles = (selectedTranscript.audioFiles && selectedTranscript.audioFiles.length > 0)
          ? selectedTranscript.audioFiles
          : (selectedTranscript.s3Files ?? selectedTranscript.files ?? []).filter((f: TranscriptFile) =>
              AUDIO_EXTS.some(ext => (f.name || '').toLowerCase().endsWith(ext))
            )

        console.log('audioFiles from backend:', selectedTranscript.audioFiles)
        console.log('audioFiles to load:', audioFiles)
        console.log('s3Files:', selectedTranscript.s3Files)
        console.log('files:', selectedTranscript.files)
        console.log('audioFiles found:', audioFiles)

        if (audioFiles.length > 0) {
          // Use presigned URLs for S3 files
          const urls: { name: string; url: string }[] = []
          for (const f of audioFiles) {
            try {
              const url = await getFilePresignedUrl(selectedTranscript.id, f.name)
              if (cancelled) return
              objectUrls.push(url)
              urls.push({ name: f.name, url })
            } catch {
              // skip failed ones
            }
          }
          if (!cancelled) setAudioUrls(urls)
        } else {
          // Fallback to single audio blob URL
          const nextUrl = await fetchTranscriptAudioUrl(selectedTranscript.id)
          if (cancelled) {
            URL.revokeObjectURL(nextUrl)
            return
          }
          objectUrls.push(nextUrl)
          setAudioUrls([{ name: selectedTranscript.audioFileName || 'audio', url: nextUrl }])
        }
      } catch {
        if (!cancelled) setAudioUrls([])
      }
    }

    void loadAudios()
    return () => {
      cancelled = true
      objectUrls.forEach(url => URL.revokeObjectURL(url))
    }
  }, [selectedTranscript?.audioAvailable, selectedTranscript?.id, audioLoadRequested])

  useEffect(() => {
    if (!uploading || progress >= 95) return

    const targets: Record<number, number> = {
      3: 22,    // uploading → creep to 22
      25: 38,   // submitted → creep to 38
      40: 48,   // processing → creep to 48
      50: 68,   // verifying → creep to 68
      70: 76,   // post_processing → creep to 76
      78: 90,   // saving → creep to 90
      92: 95,   // near complete → creep to 95
    }

    // Find the nearest ceiling to creep toward
    const ceiling = Object.entries(targets)
      .map(([from, to]) => ({ from: Number(from), to }))
      .filter(({ from }) => progress >= from)
      .sort((a, b) => b.from - a.from)[0]?.to ?? 95

    if (progress >= ceiling) return

    const timer = setInterval(() => {
      setProgress(p => {
        const next = p + 0.4
        return next >= ceiling ? ceiling : next
      })
    }, 120)

    return () => clearInterval(timer)
  }, [uploading, progress])

  useEffect(() => {
    if (!postProcessJob || postProcessJob.status === 'completed' || postProcessJob.status === 'failed') return

    const activeJobId = postProcessJob.id
    const initialStatus = postProcessJob.status
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function pollPostProcessJob() {
      let nextStatus: UploadJob['status'] | null = null
      try {
        const job = await getUploadJob(activeJobId)
        if (cancelled) return
        nextStatus = job.status
        setPostProcessJob(job)
        setPostProcessProgress(p => boundedJobProgress(job.status, p))

        if (job.status === 'completed') {
          setPostProcessing(false)
          setPostProcessBackground(false)
          setPostProcessItemId(null)
          setPostProcessProgress(100)
          if (job.transcript) {
            setTranscripts(items => items.map(item => item.id === job.transcript!.id ? job.transcript! : item))
            setSelectedTranscriptId(job.transcript.id)
            setTranscriptView(job.transcript.hasSummary ? 'proceeding' : 'translations')
          }
          showToast('Proceeding and translations are ready')
        }

        if (job.status === 'failed') {
          setPostProcessing(false)
          setPostProcessBackground(false)
          setPostProcessItemId(null)
          setPostProcessError(jobErrorMessage(job, 'Backend processing failed.'))
          showToast('Proceeding / translation failed', 'error')
        }
      } catch (err) {
        if (!cancelled) {
          setPostProcessing(false)
          setPostProcessError(displayError(err))
        }
      } finally {
        if (!cancelled && (!nextStatus || !TERMINAL_JOB_STATUSES.has(nextStatus))) {
          timer = setTimeout(pollPostProcessJob, uploadPollDelay(nextStatus ?? initialStatus))
        }
      }
    }

    pollPostProcessJob()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [postProcessJob?.id])

  function showToast(msg: string, type: 'success' | 'error' = 'success') {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  function handleFiles(files: File[]) {
    setUploadError('')
    const valid: File[] = []
    let totalSize = 0

    for (const file of files) {
      const ext = '.' + file.name.split('.').pop()!.toLowerCase()
      if (!SUPPORTED.includes(ext)) {
        setUploadError(`Unsupported file type: ${file.name}`)
        return
      }
      totalSize += file.size
      if (totalSize > MAX_SIZE) {
        setUploadError('Total size too large. Maximum is 2 GB.')
        return
      }
      valid.push(file)
    }

    setSelectedFiles(valid)

    // Auto-generate names with _1, _2, _3 suffix for multiple files
    const { date, time } = getNow()
    const safe = caseNumber.trim().replace(/\//g, '-').replace(/\s+/g, '_')
    const court = (user?.court || 'court').replace(/\s+/g, '_')
    const base = safe
      ? `${court}_${safe}_${date}_${time}`
      : `${court}_${date}_${time}`

    const names = valid.map((file, i) => {
      const ext = '.' + file.name.split('.').pop()!.toLowerCase()
      return valid.length === 1
        ? `${base}${ext}`
        : `${base}_${i + 1}${ext}`
    })
    setCustomNames(names)
  }

  function handleDrag(e: React.DragEvent) {
    e.preventDefault()
    setDragActive(true)
  }

  function handleDragLeave() {
    setDragActive(false)
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragActive(false)
    const files = Array.from(e.dataTransfer.files)
    if (files.length) handleFiles(files)
  }


  function toggleTranslation(code: string) {
    setTranslationLanguages(current => (
      current.includes(code) ? current.filter(item => item !== code) : [...current, code]
    ))
  }

  function seekAudioTo(seconds: number | null) {
    if (seconds === null) return
    const audio = audioRef.current
    if (!audio) return

    audio.currentTime = seconds
    setAudioCurrentTime(seconds)
    audio.play().catch(() => undefined)
  }

  async function handleUpload() {
    if (!caseNumber.trim()) { setUploadError('Please enter a case number.'); return }
    if (!selectedFiles.length) { setUploadError('Please select at least one audio file.'); return }

    setUploadError('')
    setUploading(true)
    setProgress(3)
    setProcessingMessageIndex(0)
    setUploadSuccess(null)
    setUploadJob(null)

    try {
      const job = await uploadAudio({
        files: selectedFiles,
        caseNumber: caseNumber.trim(),
        hearingDate,
      })
      if (job.status === 'failed') {
        throw new Error(jobErrorMessage(job, 'Backend processing failed.'))
      }
      const acceptedAt = getNow()
      setUploadJob(job)
      pendingSuccessRef.current = {
        caseNumber: caseNumber.trim(),
        date: hearingDate,
        time: acceptedAt.time.replace(/-/g, ':'),
        fileName: job.fileName ?? customNames[0] ?? '',
        fileNames: job.fileNames ?? customNames,
        path: buildCourtStoragePath(undefined, {
          court: user?.court ?? '',
          date: hearingDate,
          caseNumber: caseNumber.trim(),
          fileName: job.fileName ?? customNames[0] ?? '',
          sourcePath: 'Court files',
        }),
        size: selectedFiles.length
          ? formatSize(selectedFiles.reduce((sum, f) => sum + f.size, 0))
          : '',
      }
      setUploadComplete(true)
      
    } catch (err) {
      setUploading(false)
      setProgress(0)
      setUploadError(displayError(err))
      showToast(displayError(err, 'Upload failed'), 'error')
    }
  }

  function resetUpload() {
    submittedToastShown.current = false 
    setCaseNumber('')
    setSelectedFiles([])
    setCustomNames([])
    setUploadError('')
    setUploading(false)
    setProgress(0)
    setUploadSuccess(null)
    setUploadJob(null)
    setUploadComplete(false) 
    setTranslationLanguages([])
    setIncludeSummary(false)
    setPostProcessJob(null)
    setPostProcessing(false)
    setPostProcessProgress(0)
    setPostProcessError('')
    setPostProcessBackground(false)
    setPostProcessItemId(null) 
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  function startEditing() {
    if (!selectedTranscript) return
    setEditedText(selectedTranscript.finalizedTranscriptText || selectedTranscript.editedTranscriptText || selectedTranscript.transcriptText)
    setIsEditing(true)
  }

  function replaceEditorSelection(target: HTMLTextAreaElement, start: number, end: number, value: string) {
    const next = `${editedText.slice(0, start)}${value}${editedText.slice(end)}`
    setEditedText(next)
    requestAnimationFrame(() => {
      target.selectionStart = start + value.length
      target.selectionEnd = start + value.length
    })
  }

  function handleTranscriptEditorKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (keyboardMode === 'wij9') {
      const value = getWij9Char(e)
      if (!value) return

      e.preventDefault()
      const target = e.currentTarget
      const start = target.selectionStart ?? editedText.length
      const end = target.selectionEnd ?? editedText.length
      replaceEditorSelection(target, start, end, value)
      return
    }

    if (keyboardMode !== 'helakuru') return
    if (e.metaKey || e.ctrlKey || e.altKey) return

    const delimiters: Record<string, string> = {
      ' ': ' ',
      Enter: '\n',
      Tab: '',
      '.': '.',
      ',': ',',
      ';': ';',
      ':': ':',
      '?': '?',
      '!': '!',
      ')': ')',
      ']': ']',
    }
    if (!(e.key in delimiters)) return

    const target = e.currentTarget
    const start = target.selectionStart ?? editedText.length
    const end = target.selectionEnd ?? editedText.length
    const before = editedText.slice(0, start)
    const match = before.match(/([A-Za-z]+)$/)
    if (!match && e.key === 'Tab') return

    e.preventDefault()
    const delimiter = delimiters[e.key]
    if (!match) {
      replaceEditorSelection(target, start, end, delimiter)
      return
    }

    const token = match[1]
    const converted = helakuruToSinhalaToken(token)
    const replaceStart = start - token.length
    replaceEditorSelection(target, replaceStart, end, converted + delimiter)
  }

  async function handleDownload(version: string) {
    if (!selectedTranscript) return
    setDownloadingVersion(version) 
    try {
      await downloadTranscript(selectedTranscript.id, version)
    } catch (err) {
      showToast(displayError(err, 'Download failed'), 'error')
    } finally {
      setDownloadingVersion(null)
    }
  }
  async function handleSaveEditedTranscript() {
    if (!selectedTranscript) return
    setEditSaving(true)
    try {
      const updated = await saveEditedTranscript(selectedTranscript.id, editedText)
      setTranscripts(items => items.map(item => item.id === updated.id ? updated : item))
      setSelectedTranscriptId(updated.id)
      setIsEditing(false)
      showToast('Edited transcript saved')
    } catch (err) {
      showToast(displayError(err), 'error')
    } finally {
      setEditSaving(false)
    }
  }

  async function handleFinalizeTranscript() {
    if (!selectedTranscript) return
    const content = isEditing ? editedText : transcriptDisplayText
    if (!content.trim()) {
      showToast('Transcript is empty', 'error')
      return
    }
    const confirmed = window.confirm('Are you sure this is the finalized version of the transcript? Proceeding and translations will be generated from this verified text.')
    if (!confirmed) return

    setFinalizing(true)
    try {
      const updated = await finalizeTranscript(selectedTranscript.id, content)
      setTranscripts(items => items.map(item => item.id === updated.id ? updated : item))
      setSelectedTranscriptId(updated.id)
      setEditedText(updated.finalizedTranscriptText || content)
      showToast('Transcript finalized')
    } catch (err) {
      showToast(displayError(err), 'error')
    } finally {
      setFinalizing(false)
    }
  }

  async function handleGenerateVerifiedOutputs() {
    if (!selectedTranscript) return
    if (!selectedTranscript.finalized) {
      showToast('Verify the transcript before generating proceeding or translations', 'error')
      return
    }
    if (!translationLanguages.length && !includeSummary) {
      setPostProcessError('Select at least one translation language or proceeding.')
      return
    }

    setPostProcessError('')
    setPostProcessing(true)
    setPostProcessProgress(8)
    setPostProcessJob(null)
    try {
      const job = await generateVerifiedOutputs(selectedTranscript.id, translationLanguages, includeSummary)
      setPostProcessJob(job)
      setPostProcessProgress(progressForStatus(job.status))
      setPostProcessItemId(selectedTranscript.id) 
      showToast('Verified output generation started')

      setTimeout(() => {
        setPostProcessProgress(100)
        setTimeout(() => {
          setPostProcessing(false)
          setPostProcessBackground(true)
          setPostProcessProgress(0)
        }, 400)
      }, 800)

    } catch (err) {
      setPostProcessing(false)
      setPostProcessProgress(0)
      setPostProcessError(displayError(err))
      showToast('Could not start generation', 'error')
    }
  }

  async function handleProfileSave() {
    setProfileError('')
    setProfileSuccess('')

    if (!profileForm.displayName.trim()) {
      setProfileError('Full name is required.')
      return
    }
    if (!profileForm.currentPw.trim()) {
      setProfileError('Current password is required to save changes.')
      return
    }
    if (profileForm.newPw && profileForm.newPw.length < 6) {
      setProfileError('New password must be at least 6 characters.')
      return
    }
    if (profileForm.newPw && profileForm.newPw !== profileForm.confirmPw) {
      setProfileError('New passwords do not match.')
      return
    }

    setProfileLoading(true)
    try {
      const firebaseUser = auth.currentUser
      if (!firebaseUser || !firebaseUser.email) throw new Error('Not authenticated')

      const credential = EmailAuthProvider.credential(firebaseUser.email, profileForm.currentPw)
      await reauthenticateWithCredential(firebaseUser, credential)

      const newUsername = profileForm.username.trim().toLowerCase()
      if (newUsername !== user?.username) {
        if (!newUsername) {
          setProfileError('Username cannot be empty.')
          setProfileLoading(false)
          return
        }

        const currentUserSnap = await getDoc(doc(db, 'users', firebaseUser.uid))
        const currentUsername = currentUserSnap.data()?.username ?? ''
        const existingSnap = await getDoc(doc(db, 'usernames', newUsername))
        if (existingSnap.exists() && existingSnap.data().uid !== firebaseUser.uid) {
          setProfileError('This username is already taken.')
          setProfileLoading(false)
          return
        }

        if (currentUsername) await deleteDoc(doc(db, 'usernames', currentUsername))
        await setDoc(doc(db, 'usernames', newUsername), { email: firebaseUser.email, uid: firebaseUser.uid })
        await updateDoc(doc(db, 'users', firebaseUser.uid), { username: newUsername })
      }

      await updateDoc(doc(db, 'users', firebaseUser.uid), { displayName: profileForm.displayName.trim() })

      if (profileForm.email !== user?.email) {
        await updateEmail(firebaseUser, profileForm.email.trim())
        await updateDoc(doc(db, 'users', firebaseUser.uid), { email: profileForm.email.trim().toLowerCase() })
      }

      if (profileForm.newPw) await updatePassword(firebaseUser, profileForm.newPw)

      setProfileSuccess('Profile updated successfully.')
      setProfileForm(form => ({ ...form, currentPw: '', newPw: '', confirmPw: '' }))
      await refreshUser()
      showToast('Profile updated successfully')
    } catch (err: unknown) {
      const code = (err as { code?: string }).code
      if (code === 'auth/wrong-password' || code === 'auth/invalid-credential') {
        setProfileError('Current password is incorrect.')
      } else if (code === 'auth/email-already-in-use') {
        setProfileError('This email is already in use.')
      } else {
        setProfileError('Failed to update profile. Please try again.')
      }
    } finally {
      setProfileLoading(false)
    }
  }

  return (
    <div className="upload-page">

      <Header />

      {/* Nav tabs */}
      <nav className="upload-nav">
        {([
          { key: 'upload',  label: 'Upload audio', icon: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12' },
          { key: 'files',   label: 'Court files', icon: 'M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z' },
          { key: 'profile', label: 'My profile', icon: 'M20 21a8 8 0 0 0-16 0M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z' },
        ] as { key: Tab; label: string; icon: string }[]).map(tab => (
          <div
            key={tab.key}
            className={`upload-nav-item ${activeTab === tab.key ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.key)}
          >
            <svg width="14" height="14" viewBox="0 0 24 24"
                 fill="none" stroke="currentColor" strokeWidth="2">
              <path d={tab.icon}/>
            </svg>
            {tab.label}
          </div>
        ))}
      </nav>

      <main className="upload-main">

        {/* ══ UPLOAD TAB ════ */}
        {activeTab === 'upload' && (
          <>
            {uploadSuccess ? (
              <div className="success-card">
                <div className="success-icon-wrap">
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#3B6D11" strokeWidth="2">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </div>
                <h2 className="success-title">Audio Uploaded successfully</h2>
                <p className="success-sub">Transcript will appear in Court Files once processing is complete.</p>

                <div className="success-details">
                  <div className="success-row">
                    <span className="sr-label">Court</span>
                    <span className="sr-value">{user?.court}</span>
                  </div>
                  <div className="success-row">
                    <span className="sr-label">Case number</span>
                    <span className="sr-value">{uploadSuccess.caseNumber}</span>
                  </div>
                  <div className="success-row">
                    <span className="sr-label">Date</span>
                    <span className="sr-value">{uploadSuccess.date}</span>
                  </div>
                  <div className="success-row">
                    <span className="sr-label">Time</span>
                    <span className="sr-value">{uploadSuccess.time}</span>
                  </div>
                  <div className="success-row">
                    <span className="sr-label">File size</span>
                    <span className="sr-value">{uploadSuccess.size}</span>
                  </div>
                  <div className="success-row">
                    <span className="sr-label">
                      {uploadSuccess.fileNames.length > 1 ? 'File names' : 'File name'}
                    </span>
                    <span className="sr-value sr-mono" style={{ textAlign: 'right' }}>
                      {uploadSuccess.fileNames.length > 1 ? (
                        <span style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                          {uploadSuccess.fileNames.map((name, i) => (
                            <span key={i}>{name}</span>
                          ))}
                        </span>
                      ) : (
                        uploadSuccess.fileName
                      )}
                    </span>
                  </div>
                </div>

                <div className="success-path-box">
                  <div className="spb-label">Saved to</div>
                  <div className="spb-value">{uploadSuccess.path}</div>
                </div>

                <button 
                  type="button" 
                  className="btn-outline-full" 
                  onClick={resetUpload}
                >
                  Upload another file
                </button>
              </div>
            ) : (
              /* Upload form */
              <div className="upload-card">
                <div className="upload-card-header">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#185FA5" strokeWidth="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
                  </svg>
                  <div>
                    <div className="uch-title">Upload court audio</div>
                    <div className="uch-sub">{user?.court}</div>
                  </div>
                </div>

                {/* Case number */}
                <div className="form-field">
                  <label className="field-label">Case number</label>
                  <input type="text" className="field-input" placeholder="e.g. 123/26/MR" value={caseNumber} onChange={e => setCaseNumber(e.target.value)} />
                  <div className="field-hint">Format: 123/26/XX</div>
                </div>

                {/* Drop zone */}
                <div className="form-field">
                  <label className="field-label">Audio files</label>
                  <div
                    className={`dropzone ${dragActive ? 'active' : ''} ${selectedFiles.length > 0 ? 'has-file' : ''}`}
                    onClick={() => fileInputRef.current?.click()}
                    onDragOver={handleDrag}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                  >
                    {selectedFiles.length > 0 ? (
                      <div className="dz-files-list">
                        {selectedFiles.map((file, i) => (
                          <div key={i} className="dz-file-info">
                            <div className="dz-file-icon">
                              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#185FA5" strokeWidth="1.8">
                                <path d="M9 18V5l12-2v13" />
                                <circle cx="6" cy="18" r="3" />
                                <circle cx="18" cy="16" r="3" />
                              </svg>
                            </div>
                            <div style={{ flex: 1 }}>
                              <div className="dz-fname">{file.name}</div>
                              <div className="dz-fmeta">
                                {formatSize(file.size)} · {file.name.split('.').pop()!.toUpperCase()}
                              </div>
                            </div>
                            <button
                              type="button"
                              className="dz-remove"
                              onClick={e => {
                                e.stopPropagation()
                                const newFiles = selectedFiles.filter((_, idx) => idx !== i)
                                const newNames = customNames.filter((_, idx) => idx !== i)
                                setSelectedFiles(newFiles)
                                setCustomNames(newNames)
                                if (newFiles.length === 0 && fileInputRef.current) {
                                  fileInputRef.current.value = ''
                                }
                              }}
                            >
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <line x1="18" y1="6" x2="6" y2="18" />
                                <line x1="6" y1="6" x2="18" y2="18" />
                              </svg>
                            </button>
                          </div>
                        ))}
                        <div className="dz-add-more" onClick={e => { e.stopPropagation(); fileInputRef.current?.click() }}>
                          + Add more files
                        </div>
                      </div>
                    ) : (
                      <div className="dz-empty-content">
                        <div className="dz-icon">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" strokeWidth="1.5">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
                          </svg>
                        </div>
                        <div className="dz-text">
                          <div className="dz-title">Drag and drop audio files</div>
                          <div className="dz-browse">or click to browse - multiple files supported</div>
                        </div>
                        <div className="dz-sub">
                          <div className="dz-format-list">
                            {SUPPORTED.map(ext => <span className="dz-format-chip" key={ext}>{ext}</span>)} &nbsp;|&nbsp; Max 2 GB total
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept={SUPPORTED.join(',')}
                    multiple
                    style={{ display: 'none' }}
                    onChange={e => {
                      if (e.target.files?.length) {
                        const newFiles = Array.from(e.target.files)
                        const combined = [...selectedFiles, ...newFiles]
                        handleFiles(combined)
                      }
                    }}
                  />
                </div>

                {/* File names — editable */}
                {selectedFiles.length > 0 && (
                  <div className="rename-section">
                    <label className="field-label">
                      Audio file names
                      <span className="rename-badge">Editable</span>
                    </label>
                    {customNames.map((name, i) => (
                      <div key={i} className="rename-input-wrap" style={{ marginBottom: '8px' }}>
                        <input
                          type="text"
                          className="field-input"
                          value={name}
                          onChange={e => {
                            const updated = [...customNames]
                            updated[i] = e.target.value
                            setCustomNames(updated)
                          }}
                          placeholder={`File ${i + 1} name`}
                        />
                      </div>
                    ))}
                    <div className="field-hint">
                      {selectedFiles.length > 1
                        ? 'Files are numbered automatically. You can edit each name.'
                        : 'You can edit the file name before uploading.'}
                    </div>
                  </div>
                )}

                {/* Auto details */}
                <div className="auto-section">
                  <div className="auto-section-title">Auto-filled details</div>
                  <div className="auto-grid">

                    <div className="auto-item"><div className="ai-label">Court</div><div className="ai-value">{user?.court}</div></div>
                    <div className="auto-item">
                      <div className="ai-label">Hearing date</div>
                      <div className="date-picker-wrap">
                        <button type="button" className="date-picker-btn" onClick={() => setShowCalendar(show => !show)}>
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
                            <line x1="16" y1="2" x2="16" y2="6" />
                            <line x1="8" y1="2" x2="8" y2="6" />
                            <line x1="3" y1="10" x2="21" y2="10" />
                          </svg>
                          {hearingDate}
                        </button>
                        {showCalendar && (
                          <div className="calendar-dropdown">
                            <input type="date" className="calendar-input" value={hearingDate} max={getNow().date} onChange={e => { setHearingDate(e.target.value); setShowCalendar(false) }} />
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="auto-item"><div className="ai-label">Time</div><div className="ai-value">{currentTime.replace(/-/g, ':')}</div></div>
                    <div className="auto-item"><div className="ai-label">Uploaded by</div><div className="ai-value">{user?.displayName}</div></div>
                  </div>
                </div>

                {/* Error */}
                {uploadError && <div className="upload-error">{uploadError}</div>}

                {/* Progress */}
                {uploading && (
                  <div className="progress-wrap">
                    <div className="progress-label">
                      <span>{uploadProgressMessage}</span>
                      <span>{visibleUploadProgress}%</span>
                    </div>
                    <div className="progress-track">
                      <div className="progress-fill" style={{ width: `${visibleUploadProgress}%` }} />
                    </div>
                  </div>
                )}

                {(uploadJob?.status === 'submitted' || uploadJob?.status === 'processing') && !uploading? (
                  <div className="processing-notice">
                    <div className="processing-notice-icon"></div>
                    <div>
                      <div className="processing-notice-title">Transcribing this file</div>
                      <div className="processing-notice-sub">
                        You can upload another file or close this page - 
                        the transcript will appear in Court files when ready.
                      </div>
                    </div>
                    <button type="button" className="btn-outline-small" onClick={resetUpload}>
                      Upload another file
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="btn-upload"
                    onClick={handleUpload}
                    disabled={uploading || progress > 0 && progress < 100 || (progress === 100 && !uploadSuccess)}
                  >
                    {uploading || (progress > 0 && !uploadSuccess) ? 'Uploading...' : 'Upload Audio'}
                  </button>
                )}
              </div>
            )}
          </>
        )}

        {/* ══ COURT FILES TAB ══= */}
        {activeTab === 'files' && (
          <div className="section-card files-card">
            <div className="section-header files-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#185FA5" strokeWidth="2">
                  <path d='M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z' />
                </svg>
                <div>
                  <div className="section-title">Court files</div>
                  <div className="section-sub">{user?.court} - Transcripts </div>
                </div>
              </div>
              <div className="files-date-filter">
                <label>Date</label>
                <CourtDatePicker
                  value={filesDate}
                  onChange={setFilesDate}
                  availableDates={availableFileDates}
                  disabled={fileDatesLoading && !fileDatesLoaded}
                />
              </div>
            </div>

            {filesError && <div className="files-error">{filesError}</div>}

            <div className="files-layout">
              <aside className="files-list">
                <div className="files-list-head">
                  <span>{filesDate}</span>
                  <span>{filesLoading ? 'Loading' : `${transcripts.length} file(s)`}</span>
                </div>
                {transcripts.length === 0 && !filesLoading ? (
                  <div className="files-empty">No transcripts for this date.</div>
                ) : (
                  transcripts.map(item => (
                    <button
                      type="button"
                      key={item.id}
                      className={`file-row ${selectedTranscript?.id === item.id ? 'active' : ''}`}
                      onClick={() => setSelectedTranscriptId(item.id)}
                    >
                      <span className="file-title">{item.title || item.folderName}</span>
                      <span className="file-meta">{[item.date, item.audioFileName || fileBaseName(item.sourceFile) || item.folderName].filter(Boolean).join(' - ')}</span>
                      <span className="file-flags">
                        {item.audioAvailable && <span className="flag-audio">Transcript</span>}
                        {item.hasEditedTranscript && <span className="flag-edited">Edited</span>}
                        {item.hasSummary && <span className="flag-summary">Proceeding</span>}
                        {item.translations.length > 0 && <span className="flag-summary">{item.translations.length} translation(s)</span>}
                      </span>
                    </button>
                  ))
                )}
              </aside>

              <section className="transcript-panel">
                {selectedTranscript ? (
                  <>
                    <div className="transcript-head">
                      <div className="transcript-head-left">
                        <div className="transcript-title">{selectedTranscript.title || selectedTranscript.folderName}</div>
                        <div className="court-file-details" aria-label="Court file details">
                          {selectedTranscriptDetails.map(row => (
                            <div className="court-file-detail" key={row.label}>
                              <span className="court-file-detail-label">{row.label}</span>
                              <span className="court-file-detail-value">{row.value}</span>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="download-panel" aria-label="Transcript downloads">
                        <div className="download-panel-label">
                          Downloads <span className="download-panel-badge">DOCX</span>
                        </div>

                        <div className="download-actions-grid">
                          {/*---Left column: transcript downloads---*/}
                          <div className="download-actions">
                            <button
                              type="button"
                              className="download-action available"
                              onClick={() => void handleDownload('original')}
                              disabled={downloadingVersion !== null}
                            >
                              <span className="download-action-icon">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1E40AF" strokeWidth="2">
                                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                  <polyline points="14 2 14 8 20 8"/>
                                  <line x1="12" y1="18" x2="12" y2="12"/>
                                  <line x1="9" y1="15" x2="12" y2="18"/>
                                  <line x1="15" y1="15" x2="12" y2="18"/>
                                </svg>
                              </span>
                              <span className="download-action-info">
                                <span className="download-action-label">
                                  {downloadingVersion === 'original' ? 'Preparing...' : 'Original Word'}
                                </span>
                                <span className="download-action-sub">Structured transcript document</span>
                              </span>
                              <svg className="download-action-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                <polyline points="7 10 12 15 17 10"/>
                                <line x1="12" y1="15" x2="12" y2="3"/>
                              </svg>
                            </button>

                            {selectedTranscript.hasEditedTranscript ? (
                              <button
                                type="button"
                                className="download-action available"
                                onClick={() => void handleDownload('edited')}
                                disabled={downloadingVersion !== null}
                              >
                                <span className="download-action-icon">
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1E40AF" strokeWidth="2">
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                    <polyline points="14 2 14 8 20 8"/>
                                    <line x1="12" y1="18" x2="12" y2="12"/>
                                    <line x1="9" y1="15" x2="12" y2="18"/>
                                    <line x1="15" y1="15" x2="12" y2="18"/>
                                  </svg>
                                </span>
                                <span className="download-action-info">
                                  <span className="download-action-label">
                                    {downloadingVersion === 'edited' ? 'Preparing...' : 'Edited Word'}
                                  </span>
                                  <span className="download-action-sub">Saved review draft</span>
                                </span>
                                <svg className="download-action-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                  <polyline points="7 10 12 15 17 10"/>
                                  <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                              </button>
                            ) : (
                              <button type="button" className="download-action unavailable" disabled>
                                <span className="download-action-icon">
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" strokeWidth="2">
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                    <polyline points="14 2 14 8 20 8"/>
                                  </svg>
                                </span>
                                <span className="download-action-info">
                                  <span className="download-action-label">Edited Word</span>
                                  <span className="download-action-sub">Save edits first to unlock</span>
                                </span>
                                <svg className="download-action-lock" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <rect x="3" y="11" width="18" height="11" rx="2"/>
                                  <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                                </svg>
                              </button>
                            )}

                            {selectedTranscript.finalized ? (
                              <button
                                type="button"
                                className="download-action available"
                                onClick={() => void handleDownload('final')}
                                disabled={downloadingVersion !== null}
                              >
                                <span className="download-action-icon">
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1E40AF" strokeWidth="2">
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                    <polyline points="14 2 14 8 20 8"/>
                                    <line x1="12" y1="18" x2="12" y2="12"/>
                                    <line x1="9" y1="15" x2="12" y2="18"/>
                                    <line x1="15" y1="15" x2="12" y2="18"/>
                                  </svg>
                                </span>
                                <span className="download-action-info">
                                  <span className="download-action-label">
                                    {downloadingVersion === 'final' ? 'Preparing...' : 'Final Word'}
                                  </span>
                                  <span className="download-action-sub">Verified final transcript</span>
                                </span>
                                <svg className="download-action-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                  <polyline points="7 10 12 15 17 10"/>
                                  <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                              </button>
                            ) : (
                              <button type="button" className="download-action unavailable" disabled>
                                <span className="download-action-icon">
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" strokeWidth="2">
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                    <polyline points="14 2 14 8 20 8"/>
                                  </svg>
                                </span>
                                <span className="download-action-info">
                                  <span className="download-action-label">Final Word</span>
                                  <span className="download-action-sub">Verify transcript first to unlock</span>
                                </span>
                                <svg className="download-action-lock" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <rect x="3" y="11" width="18" height="11" rx="2"/>
                                  <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                                </svg>
                              </button>
                            )}
                          </div>

                          <div className="download-actions-divider" />

                          {/*---Right column: generated downloads---*/}
                          <div className="download-actions">
                            {selectedTranscript.hasSummary ? (
                              <button
                                type="button"
                                className="download-action available"
                                onClick={() => void handleDownload('summary')}
                                disabled={downloadingVersion !== null}
                              >
                                <span className="download-action-icon">
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1E40AF" strokeWidth="2">
                                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                                  </svg>
                                </span>
                                <span className="download-action-info">
                                  <span className="download-action-label">
                                    {downloadingVersion === 'summary' ? 'Preparing...' : 'Proceeding'}
                                  </span>
                                  <span className="download-action-sub">Q&A format</span>
                                </span>
                                <svg className="download-action-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                  <polyline points="7 10 12 15 17 10"/>
                                  <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                              </button>
                            ) : (
                              <button type="button" className="download-action unavailable" disabled>
                                <span className="download-action-icon">
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" strokeWidth="2">
                                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                                  </svg>
                                </span>
                                <span className="download-action-info">
                                  <span className="download-action-label">Proceeding</span>
                                  <span className="download-action-sub">Generate first</span>
                                </span>
                                <svg className="download-action-lock" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <rect x="3" y="11" width="18" height="11" rx="2"/>
                                  <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                                </svg>
                              </button>
                            )}

                            {selectedTranscript.translations.length > 0 ? (
                              <button
                                type="button"
                                className="download-action available"
                                onClick={() => void handleDownload('translation_combined')}
                                disabled={downloadingVersion !== null}
                              >
                                <span className="download-action-icon">
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1E40AF" strokeWidth="2">
                                    <circle cx="12" cy="12" r="10"/>
                                    <line x1="2" y1="12" x2="22" y2="12"/>
                                    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                                  </svg>
                                </span>
                                <span className="download-action-info">
                                  <span className="download-action-label">
                                    {downloadingVersion === 'translation_combined' ? 'Preparing...' : 'Translations'}
                                  </span>
                                  <span className="download-action-sub">Translated transcript</span>
                                </span>
                                <svg className="download-action-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                  <polyline points="7 10 12 15 17 10"/>
                                  <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                              </button>
                            ) : (
                              <button type="button" className="download-action unavailable" disabled>
                                <span className="download-action-icon">
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" strokeWidth="2">
                                    <circle cx="12" cy="12" r="10"/>
                                    <line x1="2" y1="12" x2="22" y2="12"/>
                                    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                                  </svg>
                                </span>
                                <span className="download-action-info">
                                  <span className="download-action-label">Translations</span>
                                  <span className="download-action-sub">Generate first</span>
                                </span>
                                <svg className="download-action-lock" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <rect x="3" y="11" width="18" height="11" rx="2"/>
                                  <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                                </svg>
                              </button>
                            )}
                          </div>
                        </div>  
                      </div>
                    </div>

                    <div className="audio-review-bar">
                      <div className="audio-review-meta">
                        <span className={selectedTranscript.finalized ? 'review-state verified' : 'review-state'}>
                          {selectedTranscript.finalized ? 'Verified transcript' : 'Pending verification'}
                        </span>
                        {hasTimedTranscript && <span>Now {formatPlaybackTime(audioCurrentTime)}</span>}
                        {selectedTranscript.finalizedAt && <span>Finalized: {selectedTranscript.finalizedAt}</span>}
                      </div>
                      {selectedTranscript.audioAvailable ? (
                        audioUrls.length > 0 ? (
                          <div className="audio-players-list">
                            {audioUrls.map((item, i) => (
                              <div key={i} className="audio-player-item">
                                {audioUrls.length > 1 && (
                                  <div className="audio-player-label">{item.name}</div>
                                )}
                                <audio
                                  ref={i === 0 ? audioRef : undefined}
                                  controls
                                  src={item.url}
                                  preload="metadata"
                                  onLoadedMetadata={event => { if (i === 0) setAudioCurrentTime(event.currentTarget.currentTime) }}
                                  onSeeked={event => { if (i === 0) setAudioCurrentTime(event.currentTarget.currentTime) }}
                                  onTimeUpdate={event => { if (i === 0) setAudioCurrentTime(event.currentTarget.currentTime) }}
                                />
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="audio-unavailable">Loading audio previews...</div>
                        )
                      ) : (
                        <div className="audio-unavailable">Audio preview is unavailable.</div>
                      )}
                    </div>

                    <div className="viewer-tabs">
                      <div className="viewer-tabs-primary">
                        <button type="button" className={transcriptView === 'transcript' ? 'active' : ''} onClick={() => setTranscriptView('transcript')}>
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                            <polyline points="14 2 14 8 20 8"/>
                            <line x1="16" y1="13" x2="8" y2="13"/>
                            <line x1="16" y1="17" x2="8" y2="17"/>
                            <polyline points="10 9 9 9 8 9"/>
                          </svg>
                          Transcript
                        </button>
                        <button type="button" className={transcriptView === 'proceeding' ? 'active' : ''} onClick={() => setTranscriptView('proceeding')} disabled={!selectedTranscript.hasSummary}>
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                          </svg>
                          Proceeding
                        </button>
                        <button type="button" className={transcriptView === 'translations' ? 'active' : ''} onClick={() => setTranscriptView('translations')} disabled={selectedTranscript.translations.length === 0}>
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <circle cx="12" cy="12" r="10"/>
                            <line x1="2" y1="12" x2="22" y2="12"/>
                            <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                          </svg>
                          Translations
                        </button>
                      </div>
                      {!isEditing && (
                        <button type="button" className="transcript-edit-button" onClick={startEditing}>
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5">
                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                          </svg>
                          Edit transcript
                        </button>
                      )}
                    </div>

                    {transcriptView === 'transcript' && (
                      <div className="transcript-body">
                        {isEditing ? (
                          <>
                            <div className="editor-toolbar">
                              <div className="segmented-control">
                                <button type="button" className={keyboardMode === 'english' ? 'active' : ''} onClick={() => setKeyboardMode('english')}>English</button>
                                <button type="button" className={keyboardMode === 'wij9' ? 'active' : ''} onClick={() => setKeyboardMode('wij9')}>Sinhala Wijeskara</button>
                                <button type="button" className={keyboardMode === 'helakuru' ? 'active' : ''} onClick={() => setKeyboardMode('helakuru')}>Phonetic</button>
                              </div>
                              <div className="editor-actions">
                                <button type="button" className="btn-lite" onClick={() => { setIsEditing(false); setEditedText(selectedTranscript.finalizedTranscriptText || selectedTranscript.editedTranscriptText || selectedTranscript.transcriptText) }}>Cancel</button>
                                <button type="button" className="btn-lite" onClick={handleSaveEditedTranscript} disabled={editSaving}>{editSaving ? 'Saving...' : 'Save draft'}</button>
                                <button type="button" className="btn-primary-small" onClick={handleFinalizeTranscript} disabled={finalizing}>{finalizing ? 'Finalizing...' : 'Verify final'}</button>
                              </div>
                            </div>
                            <textarea className="transcript-editor" value={editedText} onChange={e => setEditedText(e.target.value)} onKeyDown={handleTranscriptEditorKeyDown} spellCheck={false} />
                            <div className="verification-panel editor-verification-panel">
                              <div>
                                <div className="verification-title">Review and verify transcript</div>
                                <div className="verification-copy">Listen to the uploaded audio, edit the transcript, then verify the final text before generating proceeding or translations.</div>
                              </div>
                              <button type="button" className="btn-primary-small" onClick={handleFinalizeTranscript} disabled={finalizing}>
                                {finalizing ? 'Finalizing...' : selectedTranscript.finalized ? 'Verify done' : 'Verify final transcript'}
                              </button>
                            </div>
                            {selectedTranscript.finalized && (
                              <div className="processing-options final-options editor-final-options">
                                <div className="processing-title">Proceeding and translations from verified transcript</div>
                                <div className="option-row">
                                  {TRANSLATION_CHOICES.map(choice => (
                                    <label key={choice.code} className="option-check">
                                      <input type="checkbox" checked={translationLanguages.includes(choice.code)} onChange={() => toggleTranslation(choice.code)} disabled={postProcessing} />
                                      <span>{choice.label}</span>
                                    </label>
                                  ))}
                                  <label className="option-check option-check-strong">
                                    <input type="checkbox" checked={includeSummary} onChange={e => setIncludeSummary(e.target.checked)} disabled={postProcessing} />
                                    <span>Proceeding</span>
                                  </label>
                                  <button type="button" className="btn-primary-small" onClick={handleGenerateVerifiedOutputs} disabled={postProcessing || (translationLanguages.length > 0 && !includeSummary && !selectedTranscript.proceedingAvailable && !selectedTranscript.hasSummary)}>
                                    {postProcessing ? 'Generating...' : 'Generate'}
                                  </button>
                                </div>
                                {translationLanguages.length > 0 && !includeSummary && !selectedTranscript.proceedingAvailable && !selectedTranscript.hasSummary && (
                                  <div className="field-hint" style={{ color: '#92400e', marginTop: '6px' }}>
                                    Translations are generated from the proceeding. Please also select Proceeding, or generate it first.
                                  </div>
                                )}
                                {postProcessError && <div className="inline-error">{postProcessError}</div>}
                                {postProcessing && (
                                  <div className="progress-wrap compact-progress">
                                    <div className="progress-label"><span>{postProcessJob?.message ?? 'Generating...'}</span><span>{Math.min(postProcessProgress, 100)}%</span></div>
                                    <div className="progress-track"><div className="progress-fill" style={{ width: `${Math.min(postProcessProgress, 100)}%` }} /></div>
                                  </div>
                                )}

                                {postProcessBackground && !postProcessing && (
                                  <div className="processing-notice" style={{ marginTop: '10px' }}>
                                    <div className="processing-notice-icon" />
                                    <div>
                                      {postProcessItemId === selectedTranscript?.id ? (
                                        <>
                                          <div className="processing-notice-title">Generating in background</div>
                                          <div className="processing-notice-sub">
                                            This will appear automatically when ready.
                                          </div>
                                        </>
                                      ) : (
                                        <>
                                          <div className="processing-notice-title">Generation in progress for another case</div>
                                          <div className="processing-notice-sub">
                                            {transcripts.find(t => t.id === postProcessItemId)?.caseNumber || 'Another case'} - will appear automatically when ready.
                                          </div>
                                        </>
                                      )}
                                    </div>
                                  </div>
                                )}

                              </div>
                            )}
                          </>
                        ) : (
                          <>
                            <div className="version-badge">{selectedTranscript.finalized ? 'Verified transcript' : selectedTranscript.hasEditedTranscript ? 'Edited draft' : 'Original transcript'}</div>
                            {hasTimedTranscript ? (
                              <div className="transcript-sync-viewer" aria-label="Transcript">
                                {transcriptLines.map((line, index) => {
                                  const timed = line.start !== null && line.end !== null
                                  const active = index === activeTranscriptLineIndex
                                  return (
                                    <div
                                      ref={node => { transcriptLineRefs.current[index] = node }}
                                      className={`transcript-line ${timed ? 'timed' : ''} ${active ? 'active' : ''}`}
                                      key={line.id}
                                    >
                                      {timed ? (
                                        <button
                                          type="button"
                                          className="transcript-line-time"
                                          onClick={() => seekAudioTo(line.start)}
                                          aria-label={`Play from ${line.timeLabel}`}
                                        >
                                          {formatPlaybackTime(line.start ?? 0)}
                                        </button>
                                      ) : (
                                        <span className="transcript-line-time spacer" />
                                      )}
                                      <span className="transcript-line-text">{line.text || ' '}</span>
                                    </div>
                                  )
                                })}
                              </div>
                            ) : (
                              <pre className="transcript-pre">{transcriptDetailLoading && !selectedTranscript.contentLoaded ? 'Loading transcript...' : transcriptDisplayText || 'No transcript text found.'}</pre>
                            )}
                          </>
                        )}
                      </div>
                    )}

                    {transcriptView === 'proceeding' && (
                      <div className="transcript-body">
                        {selectedTranscript.summaryText?.startsWith('No court proceeding format') ? (
                          <div className="processing-notice" style={{ margin: '8px 0' }}>
                            <div className="processing-notice-icon" style={{ background: '#92400e', animation: 'none' }} />
                            <div>
                              <div className="processing-notice-title" style={{ color: '#92400e' }}>
                                No proceeding format found
                              </div>
                              <div className="processing-notice-sub">
                                This transcript does not contain court examination Q&A. 
                                The proceeding format applies to examination hearings only.
                              </div>
                            </div>
                          </div>
                        ) : (
                          <pre className="transcript-pre">
                            {transcriptDetailLoading && !selectedTranscript.contentLoaded 
                              ? 'Loading proceeding...' 
                              : selectedTranscript.summaryText}
                          </pre>
                        )}
                      </div>
                    )}

                    {transcriptView === 'translations' && (
                      <div className="translation-stack">
                        {selectedTranscript.translations.map(item => (
                          <div className="translation-block" key={item.filename}>
                            <div className="translation-title">{item.language}</div>
                            <pre className="transcript-pre">{item.content || (transcriptDetailLoading && !selectedTranscript.contentLoaded ? 'Loading translation...' : '')}</pre>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="empty-state">
                    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#D1D5DB" strokeWidth="1.5">
                      <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z" />
                    </svg>
                    <p>Select another date or upload audio to create transcripts.</p>
                  </div>
                )}
              </section>
            </div>
          </div>
        )}

        {/* ══ PROFILE TAB ══= */}
        {activeTab === 'profile' && (
          <div className="section-card">
            <div className="section-header files-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#185FA5" strokeWidth="2">
                  <path d="M20 21a8 8 0 0 0-16 0M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" />
                </svg>
                <div>
                  <div className="section-title">My profile</div>
                  <div className="section-sub">Update your personal information and password</div>
                </div>
              </div>
            </div>

            <div className="profile-body">

              {/* Avatar */}
              <div className="profile-avatar-row">
                <div className="profile-avatar">{(user?.displayName ?? 'U').split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase()}</div>
                <div><div className="profile-name">{user?.displayName}</div><div className="profile-court">{user?.court}</div></div>
              </div>

              {profileError && <div className="profile-error">{profileError}</div>}
              {profileSuccess && <div className="profile-success">{profileSuccess}</div>}

              {/* Personal info */}
              <div className="profile-section-title">Personal information</div>
              <div className="profile-grid">
                <div className="form-field">
                  <label className="field-label">Full name</label>
                  <input type="text" className="field-input" value={profileForm.displayName} onChange={e => setProfileForm(form => ({ ...form, displayName: e.target.value }))} />
                </div>
                <div className="form-field">
                  <label className="field-label">Email address</label>
                  <input type="email" className="field-input field-input-disabled" value={profileForm.email} disabled />
                  <div className="field-hint">Email cannot be changed. Contact admin if needed.</div>
                </div>
                <div className="form-field">
                  <label className="field-label">Username</label>
                  <input type="text" className="field-input" value={profileForm.username} onChange={e => setProfileForm(form => ({ ...form, username: e.target.value.toLowerCase().replace(/\s/g, '') }))} />
                  <div className="field-hint">Lowercase only, no spaces. Used to log in.</div>
                </div>
                <div className="form-field">
                  <label className="field-label">Court</label>
                  <input type="text" className="field-input field-input-disabled" value={user?.court ?? ''} disabled />
                  <div className="field-hint">Court assignment managed by admin.</div>
                </div>
              </div>

              {/* Change password */} 
              <div className="profile-section-title">Change password</div>
              <div className="profile-grid">
                <div className="form-field">
                  <label className="field-label">Current password</label>
                  <div className="pw-wrap">
                    <input type={showCurrPw ? 'text' : 'password'} className="field-input" placeholder="Enter current password" value={profileForm.currentPw} onChange={e => setProfileForm(form => ({ ...form, currentPw: e.target.value }))} />
                    <button type="button" className="pw-toggle" onClick={() => setShowCurrPw(show => !show)}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                        {showCurrPw ? <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24M1 1l22 22" /> : <><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></>}
                      </svg>
                    </button>
                  </div>
                  <div className="field-hint">Required to save any changes</div>
                </div>
                <div className="form-field">
                  <label className="field-label">New password</label>
                  <div className="pw-wrap">
                    <input type={showNewPw ? 'text' : 'password'} className="field-input" placeholder="Leave blank to keep current" value={profileForm.newPw} onChange={e => setProfileForm(form => ({ ...form, newPw: e.target.value }))} />
                    <button type="button" className="pw-toggle" onClick={() => setShowNewPw(show => !show)}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                        {showNewPw ? <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24M1 1l22 22" /> : <><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></>}
                      </svg>
                    </button>
                  </div>
                </div>
                <div className="form-field">
                  <label className="field-label">Confirm new password</label>
                  <input type="password" className="field-input" placeholder="Repeat new password" value={profileForm.confirmPw} onChange={e => setProfileForm(form => ({ ...form, confirmPw: e.target.value }))} />
                </div>
              </div>

              <div className="profile-actions">
                <button type="button" className="btn-save" onClick={handleProfileSave} disabled={profileLoading}>{profileLoading ? 'Saving...' : 'Save changes'}</button>
              </div>
            </div>
          </div>
        )}
      </main>

      <Footer />

      {/* Toast */}
      {toast && (
        <div className={`upload-toast ${toast.type === 'error' ? 'error' : ''}`}>
          {toast.msg}
        </div>
      )}

    </div>
  )
}
