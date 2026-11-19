import { useNavigate } from 'react-router-dom'
import { useAuth }     from '../../context/AuthContext'
import { useTheme }    from '../../context/ThemeContext'  
import './Header.css'
import logo from '../../assets/Justice-Ministry.png'
import logoDark from '../../assets/Justice-Ministry-Dark.png'

export default function Header() {
  const { user, logout } = useAuth()
  const navigate         = useNavigate()
  const { theme, toggleTheme } = useTheme()  

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  const isAdmin = user?.role === 'System Administrator'

  return (
    <header className="admin-header">
      <div className="admin-header-left">
        <div className="admin-shield">
          <img src={theme === 'dark' ? logoDark : logo} alt="Ministry of Justice Logo" className="admin-logo" />
        </div>
        <div>
          <div className="admin-header-title">Court Audio Transcription System</div>
          <div className="admin-header-sub">
            {isAdmin
              ? 'Administration Panel - Sri Lanka Judiciary'
              : `${user?.court ?? 'Staff Portal'} - Sri Lanka Judiciary`
            }
          </div>
        </div>
      </div>
      <div className="admin-header-right">
        <div className="header-avatar">
          {(user?.displayName ?? 'U').split(' ')
            .map(n => n[0]).join('').slice(0, 2).toUpperCase()}
        </div>
        <div className="header-user-info">
          <div className="header-user-name">{user?.displayName}</div>
          <div className="header-user-court">{user?.court || 'Administrator'}</div>
        </div>
        <span className="admin-badge">{user?.role}</span>

        <button
          type="button"
          className="theme-toggle-btn"
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
        <button
          type="button"
          className="admin-logout-btn"
          onClick={handleLogout}
        >
          <svg width="14" height="14" viewBox="0 0 24 24"
               fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/>
            <polyline points="16 17 21 12 16 7"/>
            <line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
          Logout
        </button>
      </div>
    </header>
  )
}