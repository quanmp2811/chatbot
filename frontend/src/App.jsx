import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { googleLogout } from '@react-oauth/google'
import { useState, useEffect } from 'react'

import MainLayout from './layouts/MainLayout'
import Documents from './pages/Documents'
import Chat from './pages/Chat'
import CompanyManagement from './pages/superadmin/CompanyManagement'
import ProfileManagement from './pages/ProfileManagement'
import DriveCallback from './pages/DriveCallback'
import LoginScreen from './pages/LoginScreen'
import CreateCompany from './pages/CreateCompany'
import CompanyPendingScreen from './pages/CompanyPendingScreen'
import DeveloperCompanies from './pages/DeveloperCompanies'
import CompanyPaymentPage from './pages/CompanyPaymentPage'
import chatApi from './api/chatApi'
import { apiUrl } from './utils/api'

const AUTH_REQUEST_TIMEOUT_MS = 15000
const AUTH_MAX_WAIT_MS = 180000
const AUTH_RETRY_DELAY_MS = 3000
const ACCOUNT_STATE_POLL_INTERVAL_MS = 5000

async function fetchWithTimeout(url, options = {}, timeoutMs = AUTH_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
    })
  } finally {
    window.clearTimeout(timeoutId)
  }
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

async function fetchWithRetry(url, options = {}, totalTimeoutMs = AUTH_MAX_WAIT_MS) {
  const deadline = Date.now() + totalTimeoutMs
  let lastError = null

  while (Date.now() < deadline) {
    try {
      return await fetchWithTimeout(
        url,
        options,
        Math.min(AUTH_REQUEST_TIMEOUT_MS, Math.max(1000, deadline - Date.now())),
      )
    } catch (error) {
      lastError = error
      const isRetryable = error?.name === 'AbortError' || error instanceof TypeError
      if (!isRetryable) throw error
      if (Date.now() + AUTH_RETRY_DELAY_MS >= deadline) break
      await sleep(AUTH_RETRY_DELAY_MS)
    }
  }

  if (lastError) throw lastError
  throw new DOMException('Timed out', 'AbortError')
}

function isRetryableResponseStatus(status) {
  return status === 408 || status === 425 || status === 429 || status === 500 || status === 502 || status === 503 || status === 504
}

async function fetchJsonWithWait(url, options = {}, totalTimeoutMs = AUTH_MAX_WAIT_MS) {
  const deadline = Date.now() + totalTimeoutMs
  let lastRetryableError = null

  while (Date.now() < deadline) {
    let response

    try {
      response = await fetchWithTimeout(
        url,
        options,
        Math.min(AUTH_REQUEST_TIMEOUT_MS, Math.max(1000, deadline - Date.now())),
      )
    } catch (error) {
      lastRetryableError = error
      const isRetryableError = error?.name === 'AbortError' || error instanceof TypeError
      if (!isRetryableError) throw error
      if (Date.now() + AUTH_RETRY_DELAY_MS >= deadline) break
      await sleep(AUTH_RETRY_DELAY_MS)
      continue
    }

    const data = await response.json().catch(() => ({}))

    if (response.ok) {
      return { response, data }
    }

    if (isRetryableResponseStatus(response.status) && Date.now() + AUTH_RETRY_DELAY_MS < deadline) {
      lastRetryableError = new Error(data?.detail || `HTTP ${response.status}`)
      await sleep(AUTH_RETRY_DELAY_MS)
      continue
    }

    const error = new Error(data?.detail || `HTTP ${response.status}`)
    error.status = response.status
    error.data = data
    throw error
  }

  if (lastRetryableError) throw lastRetryableError
  throw new DOMException('Timed out', 'AbortError')
}

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()

  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [accountError, setAccountError] = useState(null)

  const [user, setUser] = useState(null)
  const [googleToken, setGoogleToken] = useState(null)
  const [accessToken, setAccessToken] = useState(null)
  const [refreshToken, setRefreshToken] = useState(null)
  const [role, setRole] = useState(null)
  const [companyDriveToken, setCompanyDriveToken] = useState(null)
  const [companyTokenExpires, setCompanyTokenExpires] = useState(null)

  const [chats, setChats] = useState([])
  const [currentChatId, setCurrentChatId] = useState(null)

  useEffect(() => {
    const token = localStorage.getItem('googleToken')
    const access = localStorage.getItem('accessToken')
    const refresh = localStorage.getItem('refreshToken')
    const compToken = sessionStorage.getItem('companyToken')
    const compExpires = sessionStorage.getItem('companyTokenExpires')

    if (compToken) {
      setCompanyDriveToken(compToken)
      if (compExpires) setCompanyTokenExpires(compExpires)
    }

    if (token) {
      setGoogleToken(token)
      fetchMe(token).catch((err) => {
        localStorage.removeItem('googleToken')
        setGoogleToken(null)
        sessionStorage.removeItem('companyToken')
        sessionStorage.removeItem('companyTokenExpires')
        setCompanyDriveToken(null)
        setCompanyTokenExpires(null)
        if (err?.isAccountError) setAccountError(err.message)
      })
    } else if (access) {
      setGoogleToken(access)
      setRefreshToken(refresh)
      fetchMe(access).catch(async (err) => {
        if (refresh) {
          // Try to refresh token
          try {
            const refreshRes = await fetchWithRetry(apiUrl('/api/xac-thuc/refresh'), {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ refresh_token: refresh })
            })
            if (refreshRes.ok) {
              const refreshData = await refreshRes.json()
              setGoogleToken(refreshData.access_token)
              localStorage.setItem('googleToken', refreshData.access_token)
              fetchMe(refreshData.access_token)
              return
            }
          } catch {}
        }
        localStorage.removeItem('accessToken')
        localStorage.removeItem('refreshToken')
        setGoogleToken(null)
        setRefreshToken(null)
        if (err?.isAccountError) setAccountError(err.message)
      })
    }
  }, [])

  const fetchMe = async (token) => {
    const res = await fetchWithRetry(apiUrl('/api/nguoi-dung/me'), {
      headers: { Authorization: `Bearer ${token}` }
    })

    if (res.status === 401 || res.status === 403) {
      const errorData = await res.json().catch(() => ({}))
      const e = new Error(errorData.detail || 'Tài khoản bị xóa hoặc quyền bị thu hồi')
      e.isAccountError = true
      e.status = res.status
      throw e
    }
    if (!res.ok) throw new Error(`API error: ${res.status}`)

    const data = await res.json()
    const prevCompanyId = user?.company_id
    if (prevCompanyId && !data.company_id) {
      try { alert('Bạn đã bị xóa khỏi doanh nghiệp. Tài khoản sẽ đăng xuất.') } catch {}
      logout()
      const e = new Error('Bị xóa khỏi doanh nghiệp')
      e.isAccountError = true
      throw e
    }

    setUser(data)
    setRole(data.role)
    const companyToken = data.drive_token || null
    setCompanyDriveToken(companyToken)
    if (companyToken) {
      try { sessionStorage.setItem('companyToken', companyToken) } catch {}
    } else {
      try { sessionStorage.removeItem('companyToken') } catch {}
    }
    if (data.drive_token_expires) {
      setCompanyTokenExpires(data.drive_token_expires)
      try { sessionStorage.setItem('companyTokenExpires', data.drive_token_expires) } catch {}
    }
    return data
  }

  useEffect(() => {
    if (user?.company_id && googleToken) {
      const checkAccountState = () => {
        fetchMe(googleToken).catch((err) => {
          if (err?.isAccountError) {
            setAccountError(err.message)
            logout()
          }
        })
      }

      checkAccountState()

      const handleVisibilityOrFocus = () => {
        if (document.visibilityState === 'visible') {
          checkAccountState()
        }
      }

      window.addEventListener('focus', handleVisibilityOrFocus)
      document.addEventListener('visibilitychange', handleVisibilityOrFocus)

      const computeDelay = () => {
        if (companyTokenExpires) {
          const until = new Date(companyTokenExpires).getTime() - Date.now() - 60000
          return Math.max(ACCOUNT_STATE_POLL_INTERVAL_MS, until)
        }
        return ACCOUNT_STATE_POLL_INTERVAL_MS
      }

      const interval = setInterval(() => {
        checkAccountState()
      }, computeDelay())

      return () => {
        clearInterval(interval)
        window.removeEventListener('focus', handleVisibilityOrFocus)
        document.removeEventListener('visibilitychange', handleVisibilityOrFocus)
      }
    }
  }, [user?.company_id, googleToken, companyTokenExpires])

  const login = async (googleResponse) => {
    try {
      setLoading(true)
      setError(null)
      setAccountError(null)

      const idToken = googleResponse?.credential
      if (!idToken) {
        throw new Error('Không nhận được Google credential')
      }

      const { data } = await fetchJsonWithWait(apiUrl('/api/xac-thuc/dang-nhap-google'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          id_token: idToken
        })
      })

      const token = data.access_token
      if (!token) {
        throw new Error('Không nhận được access token')
      }

      setGoogleToken(token)
      localStorage.setItem('googleToken', token)
      localStorage.removeItem('refreshToken')
      await fetchMe(token)
      navigate('/chat')
    } catch (e) {
      console.error('Google login error:', e)
      const message =
        e?.name === 'AbortError'
          ? 'Backend phản hồi quá chậm quá 3 phút. Thử lại sau.'
          : e?.status === 404
          ? 'HTTP 404: Không tìm thấy endpoint đăng nhập Google trên server.'
          : e instanceof TypeError && e.message === 'Failed to fetch'
          ? 'Không kết nối được tới backend. Kiểm tra tunnel, domain và backend.'
          : (e.message || 'Đăng nhập Google thất bại')
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  const passwordLogin = async (identifier, password, rememberMe = false) => {
    try {
      setLoading(true)
      setError(null)
      setAccountError(null)

      const { data } = await fetchJsonWithWait(apiUrl('/api/xac-thuc/dang-nhap'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          identifier,
          password,
          remember_me: rememberMe
        })
      })


      const token = data.access_token
      if (!token) {
        throw new Error('Không nhận được access token')
      }

      if (rememberMe && data.refresh_token) {
        setGoogleToken(token)
        setRefreshToken(data.refresh_token)
        localStorage.setItem('googleToken', token)
        localStorage.setItem('refreshToken', data.refresh_token)
      } else {
        setGoogleToken(token)
        localStorage.setItem('googleToken', token)
      }
      await fetchMe(token)
      navigate('/chat')
    } catch (e) {
      console.error('Password login error:', e)
      const message =
        e?.name === 'AbortError'
          ? 'Backend phản hồi quá chậm quá 3 phút. Thử lại sau.'
          : e?.status === 404
          ? 'HTTP 404: Không tìm thấy endpoint đăng nhập trên server.'
          : e instanceof TypeError && e.message === 'Failed to fetch'
          ? 'Không kết nối được tới backend. Kiểm tra tunnel, domain và backend.'
          : (e.message || 'Đăng nhập thất bại')
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  const createCompany = async (formData, options = {}) => {
    setLoading(true)
    try {
      const name = String(formData.get('name') || '').trim()
      const owner_name = String(formData.get('owner_name') || '').trim()
      const creationMode = options?.creationMode === 'trial' ? 'trial' : 'paid'
      if (!name || !owner_name) {
        throw new Error('Vui lòng nhập đầy đủ thông tin doanh nghiệp')
      }

      const res = await fetch(apiUrl('/api/companies'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${googleToken}`
        },
        body: JSON.stringify({ name, owner_name, creation_mode: creationMode })
      })

      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || 'Tạo doanh nghiệp thất bại')
      }

      if (creationMode === 'trial') {
        await fetchMe(googleToken)
        navigate('/chat', { replace: true })
      } else {
        const createdCompany = data?.company || {}
        const companyName = encodeURIComponent(createdCompany.name || name)
        const message = encodeURIComponent(data?.message || 'Đăng ký doanh nghiệp thành công')
        setUser((prev) => {
          if (!prev) return prev
          return {
            ...prev,
            role: 'admin',
            company_id: createdCompany._id || prev.company_id,
            company_name: createdCompany.name || name,
            company_is_expired: true,
            company_is_blocked: false,
            company_access_state: 'expired',
            can_use_trial: prev.can_use_trial,
          }
        })
        setRole('admin')
        navigate(`/company-payment?type=renew&message=${message}&company=${companyName}`, { replace: true })
      }
      data.creation_mode = creationMode
      data.handled_navigation = true
      return data
    } finally {
      setLoading(false)
    }
  }

  const activateCompanyTrial = async () => {
    setLoading(true)
    try {
      const res = await fetch(apiUrl('/api/companies/activate-trial'), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${googleToken}`
        }
      })

      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || 'Kích hoạt dùng thử thất bại')
      }

      await fetchMe(googleToken)
      return data
    } finally {
      setLoading(false)
    }
  }

  const refreshCurrentUser = async () => {
    if (!googleToken) return null
    return fetchMe(googleToken)
  }

  const loadChats = async () => {
    if (!googleToken || !user?.company_id) return []
    try {
      const list = await chatApi.getChats(googleToken)
      const safe = Array.isArray(list) ? list : []
      setChats(safe)
      setCurrentChatId(null)
      return safe
    } catch (e) {
      console.error('Load chats error', e)
      return []
    }
  }

  useEffect(() => {
    if (googleToken && user?.company_id) {
      loadChats()
    } else {
      setChats([])
      setCurrentChatId(null)
    }
  }, [googleToken, user?.company_id])

  const handleNewChat = () => {
    setCurrentChatId(null)
    navigate('/chat')
  }

  const createNewChat = async () => {
    if (!googleToken) return null
    try {
      const created = await chatApi.createChat(googleToken)
      setChats(prev => {
        const existingIndex = prev.findIndex(chat => chat._id === created._id)
        if (existingIndex === -1) {
          return [created, ...prev]
        }
        const next = [...prev]
        next[existingIndex] = { ...next[existingIndex], ...created }
        return next
      })
      setCurrentChatId(created._id)
      navigate('/chat')
      return created
    } catch (e) {
      console.error('Create chat error', e)
      return null
    }
  }

  const selectChat = (chatId) => {
    setCurrentChatId(chatId)
    navigate('/chat')
  }

  const renameChat = async (chatId, title) => {
    if (!googleToken || !chatId || !title) return
    try {
      await chatApi.renameChat(googleToken, chatId, title)
      setChats(prev => prev.map(c => (c._id === chatId ? { ...c, title } : c)))
    } catch (e) {
      console.error('Rename chat error', e)
    }
  }

  const deleteChat = async (chatId) => {
    if (!googleToken || !chatId) return
    try {
      await chatApi.deleteChat(googleToken, chatId)
      const next = chats.filter(c => c._id !== chatId)
      setChats(next)
      if (currentChatId === chatId) {
        setCurrentChatId(next.length > 0 ? next[0]._id : null)
      }
    } catch (e) {
      console.error('Delete chat error', e)
    }
  }

  const updateChatTitle = (chatId, title) => {
    if (!chatId || !title) return
    setChats(prev => prev.map(c => (c._id === chatId ? { ...c, title } : c)))
  }

  const logout = () => {
    try { googleLogout() } catch {}
    setUser(null)
    setRole(null)
    setFiles([])
    setGoogleToken(null)
    setChats([])
    setCurrentChatId(null)
    localStorage.clear()
    sessionStorage.clear()
    navigate('/login', { replace: true })
  }

  const handleAccountErrorFromApi = (errorMsg) => {
    setAccountError(errorMsg)
    logout()
  }

  if (typeof window !== 'undefined' && window.location.pathname === '/drive-callback') {
    return <DriveCallback />
  }

  if (!user) {
    return (
      <Routes>
        <Route
          path="/login"
          element={
            <LoginScreen
              onLogin={login}
              onPasswordLogin={passwordLogin}
              loading={loading}
              error={error}
              accountError={accountError}
              onCloseAccountError={() => setAccountError(null)}
            />
          }
        />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  const isDeveloperConsoleUser = user?.role === 'developer' || (user?.role === 'super_admin' && !user?.company_id)

  if (isDeveloperConsoleUser) {
    return (
      <Routes>
        <Route
          path="/developer/companies"
          element={<DeveloperCompanies accessToken={googleToken} user={user} onLogout={logout} />}
        />
        <Route path="*" element={<Navigate to="/developer/companies" replace />} />
      </Routes>
    )
  }

  if (user?.company_access_state && user.company_access_state !== 'active') {
    return (
      <Routes>
        <Route
          path="/company-status"
          element={<CompanyPendingScreen user={user} onLogout={logout} onActivateTrial={activateCompanyTrial} loading={loading} />}
        />
        <Route
          path="/company-payment"
          element={<CompanyPaymentPage user={user} accessToken={googleToken} onPaymentSuccess={refreshCurrentUser} />}
        />
        <Route path="*" element={<Navigate to="/company-status" replace />} />
      </Routes>
    )
  }

  if (user && !user.company_id) {
    return (
      <Routes>
        <Route
          path="/create-company"
          element={
            <CreateCompany
              onSubmit={createCompany}
              loading={loading}
              onBackToLogin={logout}
              canUseTrial={Boolean(user?.can_use_trial)}
            />
          }
        />
        <Route
          path="/company-payment"
          element={<CompanyPaymentPage user={user} accessToken={googleToken} onPaymentSuccess={refreshCurrentUser} />}
        />
        <Route path="*" element={<Navigate to="/create-company" replace />} />
      </Routes>
    )
  }

  if (location.pathname === '/company-payment') {
    return (
      <Routes>
        <Route
          path="/company-payment"
          element={<CompanyPaymentPage user={user} accessToken={googleToken} onPaymentSuccess={refreshCurrentUser} />}
        />
        <Route path="*" element={<Navigate to="/company-payment" replace />} />
      </Routes>
    )
  }

  return (
    <MainLayout
      role={role}
      user={user}
      chats={chats}
      activeChatId={currentChatId}
      onNewChat={handleNewChat}
      onSelectChat={selectChat}
      onRenameChat={renameChat}
      onDeleteChat={deleteChat}
      onLogout={logout}
    >
      <Routes>
        <Route
          path="/documents"
          element={<Documents accessToken={googleToken} onAccountError={handleAccountErrorFromApi} />}
        />
        <Route
          path="/chat"
          element={
            <Chat
              accessToken={googleToken}
              currentChatId={currentChatId}
              onCreateChat={createNewChat}
              onSelectChat={selectChat}
              onChatTitleUpdated={updateChatTitle}
            />
          }
        />
        <Route
          path="/profile"
          element={<ProfileManagement accessToken={googleToken} onSaved={() => fetchMe(googleToken)} />}
        />
        {(role === 'admin' || role === 'super_admin') && (
          <Route
            path="/admin/users"
            element={<CompanyManagement accessToken={googleToken} companyTokenExpires={companyTokenExpires} user={user} />}
          />
        )}
        <Route path="*" element={<Navigate to="/chat" />} />
      </Routes>
    </MainLayout>
  )
}
