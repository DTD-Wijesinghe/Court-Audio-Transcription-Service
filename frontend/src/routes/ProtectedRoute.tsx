import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

interface Props {
  children: ReactNode
  role: 'System Administrator' | 'Staff User' | 'Judge'
}

export default function ProtectedRoute({ children, role }: Props) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-gray-500 text-sm">Loading...</p>
      </div>
    )

  }

  if (!user) return <Navigate to="/login" replace />
  const allowedRoles = role === 'Staff User'
  ? ['Staff User', 'Judge']
  : [role]

if (!allowedRoles.includes(user.role)) return <Navigate to="/login" replace />

  return <>{children}</>
}