import { initializeApp, getApps }             from 'firebase/app'
import { getAuth, createUserWithEmailAndPassword, sendEmailVerification } from 'firebase/auth'
import {
  collection, doc, getDocs, getDoc,
  setDoc, deleteDoc, orderBy, query, serverTimestamp
} from 'firebase/firestore'
import { db, firebaseConfig } from './config'

// Secondary Firebase app — creates users without signing admin out
function getSecondaryAuth() {
  const existing = getApps().find(a => a.name === 'secondary')
  const app      = existing || initializeApp(firebaseConfig, 'secondary')
  return getAuth(app)
}

export interface FirestoreUser {
  uid:         string
  email:       string
  username:    string
  displayName: string
  role:        'System Administrator' | 'Staff User' | 'Judge'
  court:       string
  createdAt:   { seconds: number } |  string | null
}

export interface CreateUserData {
  username:    string
  email:       string
  password:    string
  displayName: string
  role:        'System Administrator' | 'Staff User' | 'Judge'
  court:       string
}

export async function fetchAllUsers(): Promise<FirestoreUser[]> {
  try {
    const q    = query(collection(db, 'users'), orderBy('createdAt', 'desc'))
    const snap = await getDocs(q)
    return snap.docs.map(d => ({
      uid: d.id,
      ...d.data(),
      createdAt: d.data().createdAt ?? null,
    } as FirestoreUser))
  } catch {
    // fallback without orderBy if index not ready
    const snap = await getDocs(collection(db, 'users'))
    return snap.docs.map(d => ({
      uid: d.id,
      ...d.data(),
      createdAt: d.data().createdAt ?? null,
    } as FirestoreUser))
  }
}

// REPLACE the existing createUser function with this:
export async function createUser(data: CreateUserData): Promise<void> {
  const secondaryAuth = getSecondaryAuth()

  let uid: string

  try {
    const cred = await createUserWithEmailAndPassword(
      secondaryAuth,
      data.email.trim().toLowerCase(),
      data.password
    )
    uid = cred.user.uid

    await sendEmailVerification(cred.user)
    
  } finally {
    await secondaryAuth.signOut()
  }

  await setDoc(doc(db, 'users', uid!), {
    username:    data.username.trim().toLowerCase(),
    email:       data.email.trim().toLowerCase(),
    displayName: data.displayName.trim(),
    role:        data.role,
    court:       data.role === 'System Administrator' ? '' : data.court,
    createdAt:   serverTimestamp(),
  })

  await setDoc(doc(db, 'usernames', data.username.trim().toLowerCase()), {
    email: data.email.trim().toLowerCase(),
    uid:   uid!,
  })
}

export async function deleteUserDoc(uid: string): Promise<void> {
  const userSnap = await getDoc(doc(db, 'users', uid))

  if (userSnap.exists()) {
    const username = userSnap.data().username
    await deleteDoc(doc(db, 'usernames', username))
  }

  await deleteDoc(doc(db, 'users', uid))
}

export function formatDate(
  createdAt: { seconds: number } | string | null | undefined
): string {
  if (!createdAt) return '—'

  // Handle Firestore Timestamp object { seconds: number }
  if (typeof createdAt === 'object' && 'seconds' in createdAt) {
    const date = new Date(createdAt.seconds * 1000)
    if (isNaN(date.getTime())) return '—'
    return date.toISOString().slice(0, 10)
  }

  // Handle string dates (manually created documents)
  if (typeof createdAt === 'string') {
    const date = new Date(createdAt)
    if (isNaN(date.getTime())) return '—'
    return date.toISOString().slice(0, 10)
  }

  return '—'
}