import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useTheme } from '../context/ThemeContext'
import './Login.css'
import logo from "../assets/Justice-Ministry.png"
import logoDark from '../assets/Justice-Ministry-Dark.png'
import {sendEmailVerification } from 'firebase/auth'

import { sendPasswordResetEmail } from 'firebase/auth'
import { doc, getDoc } from 'firebase/firestore'
import { auth, db } from '../firebase/config'

export default function Login() {
  const [username, setUsername]   = useState('')
  const [password, setPassword]   = useState('')
  const [showPw,   setShowPw]     = useState(false)
  const [error,    setError]      = useState('')
  const [loading,  setLoading]    = useState(false)

  const [showResend, setShowResend] = useState(false)
  const [resendLoading, setResendLoading] = useState(false)
  const [resendSuccess, setResendSuccess] = useState(false)
  
  const [unverifiedUser, setUnverifiedUser] = useState<import('firebase/auth').User | null>(null)
  const navigate = useNavigate()
  const { user,login } = useAuth()

  const [showReset,    setShowReset]    = useState(false)
  const [resetUser,    setResetUser]    = useState('')
  const [resetMsg,     setResetMsg]     = useState('')
  const [resetError,   setResetError]   = useState('')
  const [resetLoading, setResetLoading] = useState(false)
  const { theme, toggleTheme } = useTheme()

    // Redirect if already logged in
  useEffect(() => {
    if (user) {
      navigate(user.role === 'System Administrator' ? '/admin' : '/upload', { replace: true })
    }
  }, [user, navigate])

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault()
    setError('')

    if (!username.trim() || !password.trim()) {
      setError('Please enter your username and password.')
      return
    }

    setLoading(true)

    try {
      await login(username.trim(), password)
      // navigation handled by useEffect above after user state updates
    } catch (err: unknown) {
      // Firebase errors
      const code = (err as { code?: string }).code
      if (
        code === 'auth/user-not-found'  ||
        code === 'auth/wrong-password'  ||
        code === 'auth/invalid-credential'
      ) {
        setError('Incorrect username or password. Please try again. (Error code: AUTH-0002)')
      } else if (code === 'auth/too-many-requests') {
        setError('Account temporarily locked due to too many attempts. Try again later. (Error code: AUTH-0004)')
      } else if (code === 'auth/email-not-verified') {
        setError('Your account has not been verified yet. Please check your email inbox and click the verification link. (Error code: AUTH-0005)')
        const firebaseErr = err as { code?: string; user?: import('firebase/auth').User }
        if (firebaseErr.user) {
          setUnverifiedUser(firebaseErr.user)
        }
        setShowResend(true)
      } else if (code === 'auth/network-request-failed') {
        setError('Data connection lost. Check your internet connection and try again. (Error code: NET-0001)')
      } else {
        setError(`An unexpected authentication error occurred. (Error code: AUTH-0006${code ? ` / ${code}` : ''})`)
      }
      setPassword('')
    } finally {
      setLoading(false)
    }
  }

// Update handleResend function:
async function handleResend() {
  setResendLoading(true)
  try {
    if (unverifiedUser) {
      await sendEmailVerification(unverifiedUser)
      setResendSuccess(true)
      setShowResend(false)
      setUnverifiedUser(null)
    } else {
      setError('Unable to resend. Please try logging in again.')
    }
  } catch {
    setError('Failed to resend verification email. Please try again.')
  } finally {
    setResendLoading(false)
  }
}

async function handlePasswordReset(e: React.FormEvent) {
  e.preventDefault()
  setResetError('')
  setResetMsg('')

  if (!resetUser.trim()) {
    setResetError('Please enter your username.')
    return
  }

  setResetLoading(true)
  try {
    const snap = await getDoc(
      doc(db, 'usernames', resetUser.trim().toLowerCase())
    )
    if (!snap.exists()) {
      setResetError('Username not found.')
      return
    }
    const email = snap.data().email
    await sendPasswordResetEmail(auth, email)
    setResetMsg('Password reset link sent! Check your email inbox.')
    setResetUser('')
  } catch {
    setResetError('Failed to send reset email. Please try again.')
  } finally {
    setResetLoading(false)
  }
}

  return (
    <div className="min-h-screen bg-gray-100 flex flex-col items-center justify-center px-4 py-10">
        <button
          type="button"
          className="login-theme-btn"
          onClick={toggleTheme}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {theme === 'dark' ? (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="5"/>
              <line x1="12" y1="1"  x2="12" y2="3"/>
              <line x1="12" y1="21" x2="12" y2="23"/>
              <line x1="4.22"  y1="4.22"  x2="5.64"  y2="5.64"/>
              <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
              <line x1="1"     y1="12"    x2="3"     y2="12"/>
              <line x1="21"    y1="12"    x2="23"    y2="12"/>
              <line x1="4.22"  y1="19.78" x2="5.64"  y2="18.36"/>
              <line x1="18.36" y1="5.64"  x2="19.78" y2="4.22"/>
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
            </svg>
          )}
        </button>

      {/* Logo */}
      <div className="text-center mb-7">
        <div className="flex items-center justify-center mb-1">
          <img src={theme === 'dark' ? logoDark : logo} alt="Ministry Logo" className="h-40 object-contain" />
        </div>
        <h1 className="text-[22px] font-semibold tracking-tight text-gray-900">Court Audio Transcription System</h1>
        <p className="text-[16px] text-gray-500 mt-1"> Sri Lanka Judiciary - Staff Access</p>
        <p className="text-xs text-gray-400 mt-1">Official Digital Transcription Platform</p>
      </div>

      {/* Card */}
      <div className="bg-white border border-gray-200 rounded-2xl p-7 w-full max-w-sm shadow-sm">
        <h2 className="text-base font-medium text-gray-900 mb-5">Sign in to your account</h2>

        <form onSubmit={handleLogin} className="space-y-4">

          {/* Username */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="Enter your username"
              autoComplete="username"
              className="w-full h-9 px-3 text-sm border border-gray-300 rounded-lg bg-white
                         text-gray-900 focus:outline-none focus:border-[#185FA5] focus:ring-1 focus:ring-[#185FA5] transition-colors"
            />
          </div>

          {/* Password */}
          <div>
            <div className="flex justify-between items-center mb-1.5">
              <label className="block text-xs font-medium text-gray-600">
                Password
              </label>
              <button
                type="button"
                onClick={() => { setShowReset(true); setError('') }}
                className="text-xs text-[#185FA5] hover:text-[#0C447C] bg-transparent border-none cursor-pointer"
              >
                Forgot password?
              </button>
            </div>
            <div className="relative">
              <input
                type={showPw ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Enter your password"
                autoComplete="current-password"
                className="w-full h-9 px-3 pr-10 text-sm border border-gray-300 rounded-lg bg-white
                           text-gray-900 focus:outline-none focus:border-[#185FA5] focus:ring-1 focus:ring-[#185FA5] transition-colors"
              />
              <button
                type="button"
                onClick={() => setShowPw(s => !s)}
                title={showPw ? 'Hide password' : 'Show password'}
                aria-label={showPw ? 'Hide password' : 'Show password'}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                  {showPw ? (
                    <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8
                             a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0
                             0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19
                             m-6.72-1.07a3 3 0 11-4.24-4.24M1 1l22 22" />
                  ) : (
                    <>
                      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                      <circle cx="12" cy="12" r="3" />
                    </>
                  )}
                </svg>
              </button>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700">
              {error}
            </div>
          )}

          {/* right below the error div */}
          {showResend && (
            <button
              type="button"
              onClick={handleResend}
              disabled={resendLoading}
              className="w-full h-9 border border-blue-600 text-blue-700 text-sm 
                        font-medium rounded-lg hover:bg-blue-50 transition-colors mt-2"
            >
              {resendLoading ? 'Sending...' : 'Resend verification email'}
            </button>
          )}

          {resendSuccess && (
            <div className="bg-green-50 border border-green-200 rounded-lg 
                            px-3 py-2 text-xs text-green-700 mt-2">
              Verification email sent! Please check your inbox.
            </div>
          )}

          <div className="border-t border-gray-200 my-3"></div>
          {/* Submit */}
          <button
            type="submit"
            disabled={loading}
            className="w-full h-9 text-white text-sm font-medium rounded-lg transition-colors mt-1
                      bg-[#185FA5] hover:bg-[#0C447C] tracking-wide disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>

        </form>
      </div>

      {/* Footer */}
      <p className="text-xs text-gray-400 mt-6">
        Authorised personnel only &nbsp;|&nbsp; Ministry of Justice Sri Lanka
      </p>

      {/* Password Reset Modal */}
      {showReset && (
        <div
          className="reset-overlay"
          onClick={e => {
            if (e.target === e.currentTarget) {
              setShowReset(false)
              setResetMsg('')
              setResetError('')
              setResetUser('')
            }
          }}
        >
          <div className="reset-box">

            <h2 className="reset-title">Reset password</h2>
            <p className="reset-sub">
              Enter your username and we'll send a password reset link
              to your registered email address.
            </p>

            <form onSubmit={handlePasswordReset} className="space-y-4">

              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1.5">
                  Username
                </label>
                <input
                  type="text"
                  value={resetUser}
                  onChange={e => setResetUser(e.target.value)}
                  placeholder="Enter your username"
                  className="w-full h-9 px-3 text-sm border border-gray-300
                            rounded-lg bg-white text-gray-900 focus:outline-none
                            focus:border-[#185FA5] focus:ring-1 focus:ring-[#185FA5]
                            transition-colors"
                />
              </div>

              {resetError && (
                <div className="bg-red-50 border border-red-200 rounded-lg
                                px-3 py-2 text-xs text-red-700">
                  {resetError}
                </div>
              )}

              {resetMsg && (
                <div className="bg-green-50 border border-green-200 rounded-lg
                                px-3 py-2 text-xs text-green-700">
                  {resetMsg}
                </div>
              )}

              <div className="border-t border-gray-100 pt-4 flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setShowReset(false)
                    setResetMsg('')
                    setResetError('')
                    setResetUser('')
                  }}
                  className="flex-1 h-9 text-sm font-medium text-gray-600
                            bg-gray-50 border border-gray-200 rounded-lg
                            hover:bg-gray-100 transition-colors cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={resetLoading}
                  className="flex-1 h-9 text-sm font-medium text-white
                            bg-[#185FA5] hover:bg-[#0C447C] rounded-lg
                            transition-colors disabled:opacity-50
                            disabled:cursor-not-allowed"
                >
                  {resetLoading ? 'Sending...' : 'Send reset link'}
                </button>
              </div>

            </form>
          </div>
        </div>
      )}

    </div>
  )
}
