const rawApiBaseUrl = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')

function resolveApiBaseUrl() {
  if (!rawApiBaseUrl) return ''
  if (typeof window === 'undefined') return rawApiBaseUrl

  const currentHost = window.location.hostname
  const isLocalApiTarget =
    rawApiBaseUrl.startsWith('http://localhost:') ||
    rawApiBaseUrl.startsWith('https://localhost:') ||
    rawApiBaseUrl.startsWith('http://127.0.0.1:') ||
    rawApiBaseUrl.startsWith('https://127.0.0.1:')

  // When the app is opened via tunnel/domain, a localhost API target would point
  // to the visitor machine, not this server. Fall back to same-origin backend.
  if (currentHost !== 'localhost' && currentHost !== '127.0.0.1' && isLocalApiTarget) return ''

  return rawApiBaseUrl
}

const API_BASE_URL = resolveApiBaseUrl()

export function apiUrl(path = '') {
  if (!path) return API_BASE_URL
  if (/^https?:\/\//i.test(path)) return path
  const withLeadingSlash = path.startsWith('/') ? path : `/${path}`

  // Same-origin deployments behind a reverse proxy/tunnel still need the `/api`
  // prefix so the proxy can route requests to the backend service.
  if (!API_BASE_URL) return withLeadingSlash

  const normalizedPath =
    withLeadingSlash === '/api'
      ? '/'
      : withLeadingSlash.startsWith('/api/')
        ? withLeadingSlash.slice(4)
        : withLeadingSlash

  return `${API_BASE_URL}${normalizedPath}`
}
