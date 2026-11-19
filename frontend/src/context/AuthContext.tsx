import { createContext, useContext, useState, useEffect } from 'react'
import type { ReactNode } from 'react'

import {
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signOut
} from 'firebase/auth'
import { doc, getDoc } from 'firebase/firestore'
import { auth, db } from '../firebase/config'

interface User {
  uid:         string
  username:    string
  email:       string
  role:        'System Administrator' | 'Staff User' | 'Judge'
  court:       string
  displayName: string
}

interface AuthContextType {
  user:    User | null
  loading: boolean
  login:   (username: string, password: string) => Promise<void>
  logout:  () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user,    setUser]    = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  // Auto-restore session on page refresh
  useEffect(() => {
    const unsub = onAuthStateChanged(auth, async (firebaseUser) => {
      if (firebaseUser) {
        try {
          const snap = await getDoc(doc(db, 'users', firebaseUser.uid))
          console.log('Firestore user doc exists:', snap.exists()) // ← add debug
          console.log('Firestore data:', snap.data()) 

          if (snap.exists()) {
            const data = snap.data()
            console.log('Role from Firestore:', data.role)
            setUser({
              uid:         firebaseUser.uid,
              username:    data.username,
              email:       data.email ?? '',
              role:        data.role,
              court:       data.court ?? '',
              displayName: data.displayName,
            })
          } else {
            console.log('No Firestore doc found for uid:', firebaseUser.uid)
            // Firestore doc missing — sign out
            await signOut(auth)
            setUser(null)
          }
        } catch (err){
          console.error('Firestore read error:', err)
          setUser(null)
        } finally {
          setLoading(false)
        }
      } else {
        setUser(null)
        setLoading(false)
      }
    })

    return () => unsub()
  }, [])

  async function login(username: string, password: string) {

    const snap = await getDoc(doc(db, 'usernames', username.trim().toLowerCase()))
    if (!snap.exists()) {
      throw { code: 'auth/user-not-found' }
    }
    const email = snap.data().email
    const cred  = await signInWithEmailAndPassword(auth, email, password)

    if (!cred.user.emailVerified) {
      const unverifiedUser = cred.user
      await signOut(auth)
      throw { code: 'auth/email-not-verified' , user: unverifiedUser}
    }
    
  }

  async function logout() {
    await signOut(auth)
    setUser(null)
  }

  async function refreshUser() {
  const firebaseUser = auth.currentUser
  if (!firebaseUser) return

  const snap = await getDoc(doc(db, 'users', firebaseUser.uid))
  if (snap.exists()) {
    const data = snap.data()
    setUser({
      uid:         firebaseUser.uid,
      username:    data.username,
      email:       data.email ?? '',
      role:        data.role,
      court:       data.court ?? '',
      displayName: data.displayName,
    })
  }
}

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}