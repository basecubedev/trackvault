import { NavLink } from 'react-router-dom'

/** The application's one navigation surface. */
export function Header() {
  return (
    <header className="app-header">
      <a className="skip-link" href="#content">
        Skip to content
      </a>
      <div className="app-header__brand">
        <span className="app-header__mark" aria-hidden="true">
          ▲
        </span>
        <span className="app-header__title">GPX-View</span>
      </div>
      <nav aria-label="Sections">
        <NavLink to="/" end className={({ isActive }) => (isActive ? 'is-active' : '')}>
          Dashboard
        </NavLink>
        <NavLink to="/tracks" className={({ isActive }) => (isActive ? 'is-active' : '')}>
          Tracks
        </NavLink>
        <NavLink to="/maps" className={({ isActive }) => (isActive ? 'is-active' : '')}>
          Offline maps
        </NavLink>
        <NavLink to="/credits" className={({ isActive }) => (isActive ? 'is-active' : '')}>
          Credits
        </NavLink>
        <NavLink to="/about" className={({ isActive }) => (isActive ? 'is-active' : '')}>
          About
        </NavLink>
      </nav>
    </header>
  )
}
