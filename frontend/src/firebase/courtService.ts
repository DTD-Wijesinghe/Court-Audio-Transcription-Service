import {
  collection, getDocs, addDoc,
  deleteDoc, doc, orderBy, query
} from 'firebase/firestore'
import { db } from './config'

export interface Court {
  id:   string
  name: string
}

export async function fetchCourts(): Promise<Court[]> {
  const q    = query(collection(db, 'courts'), orderBy('name', 'asc'))
  const snap = await getDocs(q)
  return snap.docs.map(d => ({ id: d.id, name: d.data().name as string }))
}

export async function addCourt(name: string): Promise<void> {
  await addDoc(collection(db, 'courts'), { name: name.trim() })
}

export async function deleteCourt(id: string): Promise<void> {
  await deleteDoc(doc(db, 'courts', id))
}