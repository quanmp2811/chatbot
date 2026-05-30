import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { GoogleOAuthProvider } from '@react-oauth/google'
import App from './App'
import 'antd/dist/reset.css'
import 'highlight.js/styles/github-dark.css'
import './index.css'

if (typeof window !== 'undefined' && window.location.hostname === '127.0.0.1') {
  const url = new URL(window.location.href)
  url.hostname = 'localhost'
  window.location.replace(url.toString())
}

if (typeof document !== 'undefined') {
  const preventGestureZoom = (event) => {
    event.preventDefault()
  }

  const preventPinchZoom = (event) => {
    if (event.touches && event.touches.length > 1) {
      event.preventDefault()
    }
  }

  document.addEventListener('gesturestart', preventGestureZoom, { passive: false })
  document.addEventListener('gesturechange', preventGestureZoom, { passive: false })
  document.addEventListener('gestureend', preventGestureZoom, { passive: false })
  document.addEventListener('touchmove', preventPinchZoom, { passive: false })
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <GoogleOAuthProvider clientId="661256286731-1nfm2k9marcon8fvftnsdjgnepu7jl63.apps.googleusercontent.com">
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </GoogleOAuthProvider>
  </React.StrictMode>
)
