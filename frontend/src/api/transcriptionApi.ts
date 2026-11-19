import { auth } from '../firebase/config'
import { errorFromResponse, networkSystemError } from './systemErrors'

export interface TranscriptTranslation {
  filename: string
  language: string
  content: string
}

export interface TranscriptFile {
  name: string
  size: number
  updatedAt: string
}

export interface TranscriptDates {
  dates: string[]
  defaultDate: string
}

export interface TranscriptItem {
  id: string
  date: string
  folderName: string
  title: string
  caseNumber: string
  court: string
  uploadedBy: string
  sourceFile: string
  audioFileName: string
  model: string
  quality: string
  updatedAt: string
  contentLoaded?: boolean
  transcriptFile: string
  transcriptText: string
  editedTranscriptText: string
  finalizedTranscriptText: string
  summaryText: string
  translations: TranscriptTranslation[]
  files: TranscriptFile[]
  s3Files: TranscriptFile[]
  audioFiles?: { name: string; s3Key: string }[]
  hasEditedTranscript: boolean
  hasFinalizedTranscript: boolean
  finalized: boolean
  finalizedAt: string
  hasSummary: boolean
  proceedingAvailable: boolean
  hasSrt: boolean
  srtFile: string
  srtText: string
  audioAvailable: boolean
  storagePath?: string
}

export interface UploadJob {
  id: string
  status: 'queued' | 'uploaded_to_backend' | 'saving_to_s3' | 'uploaded_to_s3' | 'preparing_audio' | 'enhancing_audio' | 'uploading_to_gcs' | 'chunking_audio' | 'uploading_chunks_to_gcs' | 'submitted_to_vertex' | 'uploading' | 'submitted' | 'processing' | 'transcribing' | 'downloading_results' | 'repairing_transcript' | 'merging_transcript' | 'saving_transcript_to_s3' | 'verifying' | 'post_processing' | 'saving' | 'completed' | 'failed'
  created_at?: string
  message?: string  
  updated_at?: string
  fileName?: string
  fileNames?: string[]
  fileSize?: number
  hearingDate?: string
  caseNumber?: string
  translations?: string[]
  includeSummary?: boolean
  gcsUri?: string
  transcript?: TranscriptItem
  errorCode?: string
  error?: { code: string; message: string; category?: string; retryable?: boolean }
}

interface UploadAudioParams {
  files: File[]
  caseNumber: string
  hearingDate: string
}

const API_BASE = (import.meta.env.VITE_API_URL ?? '/api').replace(/\/$/, '')

function endpoint(path: string) {
  return `${API_BASE}${path}`
}

async function authHeaders(initHeaders?: HeadersInit): Promise<Headers> {
  const headers = new Headers(initHeaders)
  const user = auth.currentUser
  if (!user) {
    throw new Error('Not authenticated')
  }

  const token = await user.getIdToken()
  headers.set('Authorization', `Bearer ${token}`)
  return headers
}

async function apiFetch<T>(path: string, init?: RequestInit, retry=true): Promise<T> {
  let res: Response
  try {
    res = await fetch(endpoint(path), {
      ...init,
      headers: await authHeaders(init?.headers),
    })
  } catch {
    throw networkSystemError()
  }

  if (res.status === 401 && retry) {
    const user = auth.currentUser
    if (user) {
      await user.getIdToken(true)  // force refresh
      return apiFetch<T>(path, init, false)  // retry once
    }
  }
  if (!res.ok) {
    throw await errorFromResponse(res)
  }
  return res.json() as Promise<T>
}

function fileNameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback

  const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1])
  }

  const match = header.match(/filename=\"?([^\";]+)\"?/i)
  if (match?.[1]) {
    return match[1]
  }

  return fallback
}

async function apiBlob(path: string, fallbackFilename: string): Promise<{ blob: Blob; filename: string }> {
  let res: Response
  try {
    res = await fetch(endpoint(path), { headers: await authHeaders() })
  } catch {
    throw networkSystemError()
  }
  if (!res.ok) {
    throw await errorFromResponse(res)
  }

  return {
    blob: await res.blob(),
    filename: fileNameFromDisposition(res.headers.get('Content-Disposition'), fallbackFilename),
  }
}

export async function listTranscripts(date: string): Promise<TranscriptItem[]> {
  const data = await apiFetch<{ date: string; items: TranscriptItem[] }>(`/transcripts?date=${encodeURIComponent(date)}`)
  return data.items
}

export async function getTranscript(itemId: string): Promise<TranscriptItem> {
  return apiFetch<TranscriptItem>(`/transcripts/${encodeURIComponent(itemId)}`)
}

export async function listTranscriptDates(): Promise<TranscriptDates> {
  return apiFetch<TranscriptDates>('/transcript-dates')
}

export async function uploadAudio(params: UploadAudioParams): Promise<UploadJob> {
  const form = new FormData()
  for (const file of params.files) {
    form.append('files', file)
  }
  form.append('case_number', params.caseNumber)
  form.append('hearing_date', params.hearingDate)

  return apiFetch<UploadJob>('/uploads', {
    method: 'POST',
    body: form,
  })
}

export async function getUploadJob(jobId: string): Promise<UploadJob> {
  return apiFetch<UploadJob>(`/jobs/${encodeURIComponent(jobId)}`)
}

export async function saveEditedTranscript(itemId: string, content: string): Promise<TranscriptItem> {
  return apiFetch<TranscriptItem>(`/transcripts/${encodeURIComponent(itemId)}/edited`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export async function downloadTranscript(itemId: string, version: string): Promise<void> {
  const extension = version === 'srt' ? 'srt' : 'docx'
  const { blob, filename } = await apiBlob(`/transcripts/${encodeURIComponent(itemId)}/download/${version}`, `transcript-${version}.${extension}`)
  const url = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = filename || `transcript-${version}`
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    URL.revokeObjectURL(url)
  }
}

export async function finalizeTranscript(itemId: string, content: string): Promise<TranscriptItem> {
  return apiFetch<TranscriptItem>(`/transcripts/${encodeURIComponent(itemId)}/finalize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export async function generateVerifiedOutputs(itemId: string, translationLanguages: string[], includeSummary: boolean): Promise<UploadJob> {
  return apiFetch<UploadJob>(`/transcripts/${encodeURIComponent(itemId)}/post-process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ translation_languages: translationLanguages, include_summary: includeSummary }),
  })
}

export async function fetchTranscriptAudioUrl(itemId: string): Promise<string> {
  const { blob } = await apiBlob(`/transcripts/${encodeURIComponent(itemId)}/audio`, 'audio-preview')
  return URL.createObjectURL(blob)
}

export async function getFilePresignedUrl(itemId: string, filename: string): Promise<string> {
  const { blob } = await apiBlob(
    `/transcripts/${encodeURIComponent(itemId)}/file?name=${encodeURIComponent(filename)}`,
    filename,
  )
  return URL.createObjectURL(blob)
}
