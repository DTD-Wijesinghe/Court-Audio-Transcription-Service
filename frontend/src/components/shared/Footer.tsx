import './Footer.css'
import sltLogo from '../../assets/slt-logo.png'

export default function Footer() {
  return (
    <footer className="site-footer">

      {/* Left — copyright */}
      <div className="footer-left">
        <span className="footer-copy">
          © {new Date().getFullYear()} Ministry of Justice - Sri Lanka Judiciary.
          All rights reserved.
        </span>
      </div>

      {/* Right — powered by SLT + version */}
      <div className="footer-right">
        <span className="footer-version">Version 2.1.1</span>
        <span className="footer-dot">·</span>
        <span className="footer-conf">Confidential</span>
        <div className="footer-divider-v" />
        <span className="footer-powered-text"><i>Developed by</i></span>
        <img
          src={sltLogo}
          alt="SLT R&D Division"
          className="footer-slt-logo"
        />
      </div>

    </footer>
  )
}