// Import the functions you need from the SDKs you need
import { initializeApp } from "firebase/app"
import { getAuth, setPersistence, browserSessionPersistence }  from 'firebase/auth';
import { getFirestore }  from 'firebase/firestore'

// TODO: Add SDKs for Firebase products that you want to use
// https://firebase.google.com/docs/web/setup#available-libraries

// Your web app's Firebase configuration
export const firebaseConfig = {
  apiKey: "AIzaSyBJu_l892gScoPI9NYVRrlaAFqjnRlzHGA",
  authDomain: "court-audio-transcriptio-c632d.firebaseapp.com",
  projectId: "court-audio-transcriptio-c632d",
  storageBucket: "court-audio-transcriptio-c632d.firebasestorage.app",
  messagingSenderId: "939694561199",
  appId: "1:939694561199:web:57b11d0a5922267e50015a"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);

export const auth = getAuth(app)
export const db   = getFirestore(app)

// Session only — user must login again after closing browser
setPersistence(auth, browserSessionPersistence)