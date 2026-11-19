import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Login from './pages/Login'
import AdminDashboard from './pages/AdminDashboard'
import Upload from './pages/Upload'
import ProtectedRoute from './routes/ProtectedRoute'
import { AuthProvider } from './context/AuthContext'
import { ThemeProvider } from './context/ThemeContext'
import SystemConnectionBanner from './components/shared/SystemConnectionBanner'
import './App.css'

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <SystemConnectionBanner />
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Navigate to="/login" replace />} />
            <Route path="/login" element={<Login />} />
            <Route path="/admin" element={
              <ProtectedRoute role="System Administrator">
                <AdminDashboard />
              </ProtectedRoute>
            } />
            <Route path="/upload" element={
              <ProtectedRoute role="Staff User">
                <Upload />
              </ProtectedRoute>
            } />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  )
}
