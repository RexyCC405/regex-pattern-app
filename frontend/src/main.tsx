import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import App from './layout/App'
import icon from "../assets/icon.png";

/** ultra-light hash router: "" -> "/", "#/app" -> "/app" */
function useHashRoute() {
  const get = () => (window.location.hash.replace(/^#/, '') || '/')
  const [route, setRoute] = useState(get())
  useEffect(() => {
    const onHash = () => setRoute(get())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return route
}

function Home() {
  return (
    <div className="center">
      <div>
        <h1 className="title">
          <img src={icon} alt="App logo" className="logo" />
          <span className="brand">Data Transformation Platform</span>
        </h1>
        <div className="stack">
          <a href="#/app" className="btn" aria-label="Get started">Get started</a>
          <a href="https://regex101.com" target="_blank" rel="noreferrer" className="btn ghost">
            Learn regex basics
          </a>
        </div>
      </div>
    </div>
  )
}


function Root() {
  const route = useHashRoute()
  return route.startsWith('/app') ? <App /> : <Home />
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)
