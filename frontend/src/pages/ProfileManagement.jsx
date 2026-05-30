import { useEffect, useState } from 'react'
import { Alert, Button, Card, Input, Modal, Space } from 'antd'
import { apiUrl } from '../utils/api'

export default function ProfileManagement({ accessToken, onSaved }) {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState('')

  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [hasPassword, setHasPassword] = useState(false)
  const [emailVerified, setEmailVerified] = useState(false)
  const [emailCode, setEmailCode] = useState('')
  const [sendingCode, setSendingCode] = useState(false)
  const [verifyingCode, setVerifyingCode] = useState(false)
  const [hasRequestedCode, setHasRequestedCode] = useState(false)
  const [codeExpireAtMs, setCodeExpireAtMs] = useState(null)
  const [remainingSeconds, setRemainingSeconds] = useState(0)
  const [verifyModalOpen, setVerifyModalOpen] = useState(false)
  const [pendingSave, setPendingSave] = useState(false)
  const [verificationDeliveryEmail, setVerificationDeliveryEmail] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [pendingPasswordDraft, setPendingPasswordDraft] = useState(null)
  const isLightTheme = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light'
  const pageTitleStyle = { color: isLightTheme ? '#1f2937' : '#fff', marginBottom: 16 }
  const labelStyle = { color: isLightTheme ? '#374151' : '#fff', marginBottom: 6, fontWeight: 600 }
  const cardStyle = isLightTheme
    ? { background: 'rgba(255, 252, 247, 0.96)', border: '1px solid #e6dfd2', boxShadow: '0 18px 42px rgba(15, 23, 42, 0.06)' }
    : { background: '#1f1f1f', border: '1px solid #303030' }
  const dividerStyle = { height: 1, background: isLightTheme ? '#e7dfd1' : '#303030', margin: '4px 0' }

  const hasPasswordInput = Boolean(newPassword || confirmPassword)

  const loadProfile = async () => {
    if (!accessToken) return

    setLoading(true)
    setError(null)

    try {
      const res = await fetch(apiUrl('/api/nguoi-dung/me'), {
        headers: { Authorization: `Bearer ${accessToken}` }
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || `Lỗi ${res.status}`)
      }

      setName(data?.name || '')
      setPhone(data?.phone || '')
      setEmail(data?.contact_email || data?.email || '')
      setHasPassword(Boolean(data?.has_password))
      setEmailVerified(Boolean(data?.email_verified))
      setEmailCode('')
      setHasRequestedCode(false)
      setCodeExpireAtMs(null)
      setVerifyModalOpen(false)
      setPendingSave(false)
      setVerificationDeliveryEmail('')
      setPendingPasswordDraft(null)
      setNewPassword('')
      setConfirmPassword('')
    } catch (e) {
      setError(e.message || 'Không tải được thông tin người dùng')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadProfile()
  }, [accessToken])

  useEffect(() => {
    if (!codeExpireAtMs) {
      setRemainingSeconds(0)
      return
    }

    const updateRemaining = () => {
      const sec = Math.max(0, Math.ceil((codeExpireAtMs - Date.now()) / 1000))
      setRemainingSeconds(sec)
    }

    updateRemaining()
    const timer = setInterval(updateRemaining, 1000)
    return () => clearInterval(timer)
  }, [codeExpireAtMs])

  const resetVerificationFlow = () => {
    setEmailVerified(false)
    setEmailCode('')
    setHasRequestedCode(false)
    setCodeExpireAtMs(null)
    setVerifyModalOpen(false)
    setPendingSave(false)
    setVerificationDeliveryEmail('')
    setPendingPasswordDraft(null)
  }

  const validateProfileForm = (passwordDraft = null) => {
    if (!email.trim()) {
      return 'Email là bắt buộc'
    }

    const draft = passwordDraft || {
      newPassword,
      confirmPassword,
    }
    const hasDraftPassword = Boolean(draft.newPassword || draft.confirmPassword)

    if (hasDraftPassword) {
      if (!draft.newPassword.trim() || !draft.confirmPassword.trim()) {
        return 'Vui lòng nhập đủ mật khẩu mới và xác nhận mật khẩu mới'
      }
      if (draft.newPassword.trim().length < 6) {
        return 'Mật khẩu mới phải từ 6 ký tự trở lên'
      }
      if (draft.newPassword !== draft.confirmPassword) {
        return 'Xác nhận mật khẩu không khớp'
      }
    }

    return null
  }

  const performProfileSave = async (passwordDraft = null) => {
    if (!accessToken) return

    const hasPasswordChange = Boolean(passwordDraft?.newPassword || passwordDraft?.confirmPassword)

    setSaving(true)
    setError(null)
    setSuccess('')

    try {
      const profileRes = await fetch(apiUrl('/api/nguoi-dung/me'), {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`
        },
        body: JSON.stringify({
          name: name.trim(),
          phone: phone.trim()
        })
      })
      const profileData = await profileRes.json().catch(() => ({}))
      if (!profileRes.ok) {
        throw new Error(profileData.detail || 'Lỗi cập nhật thông tin')
      }

      if (hasPasswordChange) {
        const passRes = await fetch(apiUrl('/api/nguoi-dung/doi-mat-khau'), {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${accessToken}`
          },
          body: JSON.stringify({
            current_password: '',
            new_password: passwordDraft.newPassword,
            confirm_password: passwordDraft.confirmPassword
          })
        })
        const passData = await passRes.json().catch(() => ({}))
        if (!passRes.ok) {
          throw new Error(passData.detail || 'Lỗi đổi mật khẩu')
        }

        setNewPassword('')
        setConfirmPassword('')
        setPendingPasswordDraft(null)
        setHasPassword(true)
      }

      setSuccess('Đã cập nhật thông tin thành công')
      onSaved?.(profileData?.user)
    } catch (e) {
      if (passwordDraft) {
        setNewPassword(passwordDraft.newPassword)
        setConfirmPassword(passwordDraft.confirmPassword)
        setPendingPasswordDraft(passwordDraft)
      }
      setError(e.message || 'Cập nhật thông tin thất bại')
    } finally {
      setSaving(false)
    }
  }

  const sendVerifyCode = async (passwordDraft = null) => {
    if (!accessToken) return false

    if (!email.trim()) {
      setError('Vui lòng nhập email để nhận mã xác thực')
      return false
    }

    setSendingCode(true)
    setError(null)
    setSuccess('')

    try {
      const res = await fetch(apiUrl('/api/nguoi-dung/email/send-code'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`
        },
        body: JSON.stringify({
          email: email.trim(),
          change_password: Boolean(passwordDraft?.newPassword || passwordDraft?.confirmPassword)
        })
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || 'Không gửi được mã xác thực')
      }

      const expiresInSeconds = Number(data?.expires_in_seconds || 0)
      if (expiresInSeconds > 0) {
        setCodeExpireAtMs(Date.now() + expiresInSeconds * 1000)
      } else {
        setCodeExpireAtMs(null)
      }

      setHasRequestedCode(true)
      setVerificationDeliveryEmail(data?.delivery_email || email.trim())
      setSuccess('Đã gửi mã xác thực')
      return true
    } catch (e) {
      if (passwordDraft) {
        setNewPassword(passwordDraft.newPassword)
        setConfirmPassword(passwordDraft.confirmPassword)
        setPendingPasswordDraft(passwordDraft)
      }
      setError(e.message || 'Không gửi được mã xác thực')
      return false
    } finally {
      setSendingCode(false)
    }
  }

  const saveProfile = async () => {
    if (!accessToken) return

    const passwordDraft = hasPasswordInput
      ? {
          newPassword,
          confirmPassword,
        }
      : null

    const validationError = validateProfileForm(passwordDraft)
    if (validationError) {
      setError(validationError)
      setSuccess('')
      return
    }

    if (passwordDraft) {
      setPendingPasswordDraft(passwordDraft)
      setNewPassword('')
      setConfirmPassword('')
    } else {
      setPendingPasswordDraft(null)
    }

    setPendingSave(true)
    setVerifyModalOpen(true)
    const sent = await sendVerifyCode(passwordDraft)
    if (!sent) {
      setVerifyModalOpen(false)
      setPendingSave(false)
    }
  }

  const verifyCode = async () => {
    if (!accessToken) return

    const validationError = validateProfileForm(pendingPasswordDraft)
    if (validationError) {
      setError(validationError)
      return
    }

    if (!email.trim() || !emailCode.trim()) {
      setError('Vui lòng nhập email và mã xác thực')
      return
    }

    setVerifyingCode(true)
    setError(null)
    setSuccess('')

    try {
      const res = await fetch(apiUrl('/api/nguoi-dung/email/verify-code'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`
        },
        body: JSON.stringify({ email: email.trim(), code: emailCode.trim() })
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || 'Xác thực mã thất bại')
      }

      setEmailVerified(true)
      setEmailCode('')
      setVerifyModalOpen(false)
      setCodeExpireAtMs(null)
      setHasRequestedCode(true)
      setVerificationDeliveryEmail('')
      onSaved?.(data?.user)

      if (pendingSave) {
        const passwordDraft = pendingPasswordDraft
        setPendingSave(false)
        await performProfileSave(passwordDraft)
      }
    } catch (e) {
      setError(e.message || 'Xác thực mã thất bại')
    } finally {
      setVerifyingCode(false)
    }
  }

  const handleCloseModal = () => {
    if (pendingPasswordDraft) {
      setNewPassword(pendingPasswordDraft.newPassword)
      setConfirmPassword(pendingPasswordDraft.confirmPassword)
    }
    setVerifyModalOpen(false)
    setPendingSave(false)
  }

  const formatCountdown = (totalSeconds) => {
    const m = Math.floor(totalSeconds / 60)
    const s = totalSeconds % 60
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }

  return (
    <div style={{ maxWidth: 780 }}>
      <h2 style={pageTitleStyle}>Quản lý tài khoản</h2>

      {error && (
        <Alert
          type="error"
          showIcon
          closable
          onClose={() => setError(null)}
          message={error}
          style={{ marginBottom: 12 }}
        />
      )}

      {success && (
        <Alert
          type="success"
          showIcon
          closable
          onClose={() => setSuccess('')}
          message={success}
          style={{ marginBottom: 12 }}
        />
      )}

      <Card
        loading={loading}
        style={cardStyle}
        bodyStyle={{ padding: 20 }}
      >
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          <div>
            <div style={labelStyle}>Họ và tên</div>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nhập họ và tên" />
          </div>

          <div>
            <div style={labelStyle}>Số điện thoại</div>
            <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Nhập số điện thoại" />
          </div>

          <div>
            <div style={labelStyle}>Email</div>
            <Input
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                resetVerificationFlow()
              }}
              placeholder="Nhập email"
            />
          </div>

          <div style={dividerStyle} />

          <div>
            <div style={labelStyle}>Mật khẩu mới</div>
            <Input.Password
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Nhập mật khẩu mới"
            />
          </div>

          <div>
            <div style={labelStyle}>Xác nhận mật khẩu mới</div>
            <Input.Password
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Nhập lại mật khẩu mới"
            />
          </div>
        </Space>
      </Card>

      <div style={{ marginTop: 16 }}>
        <Button type="primary" size="large" loading={saving} onClick={saveProfile}>
          Cập nhật
        </Button>
      </div>

      <Modal
        title="Xác thực email"
        open={verifyModalOpen}
        zIndex={2000}
        maskClosable={false}
        onCancel={handleCloseModal}
        footer={[
          <Button
            key="resend"
            onClick={() => sendVerifyCode(pendingPasswordDraft)}
            loading={sendingCode}
            disabled={remainingSeconds > 0}
          >
            {hasRequestedCode ? 'Gửi lại mã' : 'Gửi mã'}
          </Button>,
          <Button
            key="verify"
            type="primary"
            onClick={verifyCode}
            loading={verifyingCode}
          >
            Cập nhật
          </Button>
        ]}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {sendingCode && (
            <div style={{ color: '#d1d5db', fontSize: 12 }}>
              Đang gửi mã xác thực...
            </div>
          )}
          <div>
            Mã xác thực đã được gửi tới email: <strong>{verificationDeliveryEmail || email.trim()}</strong>
          </div>
          <Input
            value={emailCode}
            onChange={(e) => setEmailCode(e.target.value)}
            placeholder="Nhập mã 6 số"
          />
          <div style={{ color: remainingSeconds > 0 ? '#d97706' : '#dc2626', fontSize: 12 }}>
            {remainingSeconds > 0
              ? `Mã hết hạn sau ${formatCountdown(remainingSeconds)}`
              : 'Mã xác thực đã hết hạn, vui lòng gửi lại mã mới'}
          </div>
        </Space>
      </Modal>
    </div>
  )
}
