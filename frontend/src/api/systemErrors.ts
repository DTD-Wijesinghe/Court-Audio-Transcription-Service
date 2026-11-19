export interface SystemErrorBody {
  code: string
  message: string
  category?: string
  retryable?: boolean
  requestId?: string
}

const STATUS_ERRORS: Record<number, SystemErrorBody> = {
  400: { code: 'REQ-0001', message: 'The request contains invalid data. Check the entered values.' },
  401: { code: 'AUTH-0002', message: 'Your session is invalid or has expired. Please sign in again.' },
  403: { code: 'AUTH-0003', message: 'You do not have permission to perform this action.' },
  404: { code: 'REQ-0002', message: 'The requested record or file was not found.' },
  413: { code: 'REQ-0003', message: 'The uploaded file is too large.' },
  422: { code: 'REQ-0001', message: 'The request contains invalid data. Check the entered values.' },
  429: { code: 'SRV-0003', message: 'The server is processing the maximum number of audio jobs. Please try again shortly.', retryable: true },
  500: { code: 'SYS-0001', message: 'An unexpected system error occurred. Please try again or contact support.' },
  502: { code: 'NET-0001', message: 'Data connection lost. Check your internet connection and try again.' },
  503: { code: 'NET-0001', message: 'Data connection lost. Check your internet connection and try again.' },
  504: { code: 'NET-0002', message: 'The service connection timed out. Please try again.' },
  507: { code: 'SRV-0001', message: 'Server storage is full. Contact the system administrator.' },
}

export class SystemError extends Error {
  code: string
  category?: string
  retryable: boolean
  requestId?: string
  status?: number

  constructor(error: SystemErrorBody, status?: number) {
    super(error.message)
    this.name = 'SystemError'
    this.code = error.code
    this.category = error.category
    this.retryable = Boolean(error.retryable)
    this.requestId = error.requestId
    this.status = status
  }
}

export function displayError(error: unknown, fallback = 'An unexpected error occurred.'): string {
  if (error instanceof SystemError) {
    return `${error.message} (Error code: ${error.code})`
  }
  const firebaseCode = (error as { code?: string } | null)?.code
  if (firebaseCode === 'auth/network-request-failed' || !navigator.onLine) {
    return 'Data connection lost. Check your internet connection and try again. (Error code: NET-0001)'
  }
  if (error instanceof TypeError && /fetch|network|load failed/i.test(error.message)) {
    return 'Data connection lost. Check your internet connection and try again. (Error code: NET-0001)'
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}

export async function errorFromResponse(response: Response): Promise<SystemError> {
  let body: { error?: Partial<SystemErrorBody>; detail?: string } | undefined
  try {
    body = await response.clone().json()
  } catch {
    body = undefined
  }
  const fallback = STATUS_ERRORS[response.status] ?? STATUS_ERRORS[500]
  const supplied = body?.error
  return new SystemError({
    code: supplied?.code || fallback.code,
    message: supplied?.message || body?.detail || fallback.message,
    category: supplied?.category || fallback.category,
    retryable: supplied?.retryable ?? fallback.retryable,
    requestId: supplied?.requestId || response.headers.get('X-Request-ID') || undefined,
  }, response.status)
}

export function networkSystemError(): SystemError {
  return new SystemError({
    code: 'NET-0001',
    message: 'Data connection lost. Check your internet connection and try again.',
    category: 'network',
    retryable: true,
  })
}
