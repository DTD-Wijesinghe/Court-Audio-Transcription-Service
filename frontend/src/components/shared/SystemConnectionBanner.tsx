import { useEffect, useState } from 'react'

export default function SystemConnectionBanner() {
  const [online, setOnline] = useState(navigator.onLine)

  useEffect(() => {
    const connected = () => setOnline(true)
    const disconnected = () => setOnline(false)
    window.addEventListener('online', connected)
    window.addEventListener('offline', disconnected)
    return () => {
      window.removeEventListener('online', connected)
      window.removeEventListener('offline', disconnected)
    }
  }, [])

  if (online) return null
  return (
    <div className="system-connection-banner" role="alert" aria-live="assertive">
      Data connection lost. Check your internet connection and try again. (Error code: NET-0001)
    </div>
  )
}
