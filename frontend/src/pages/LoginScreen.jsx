import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { GoogleLogin } from "@react-oauth/google"
import { EyeInvisibleOutlined, EyeOutlined } from "@ant-design/icons"
import { apiUrl } from "../utils/api"

export default function LoginScreen({ onLogin, onPasswordLogin, error, loading }) {
  const navigate = useNavigate()
  const [identifier, setIdentifier] = useState("")
  const [password, setPassword] = useState("")
  const [showLoginPassword, setShowLoginPassword] = useState(false)
  const [rememberMe, setRememberMe] = useState(false)

  const [showRegister, setShowRegister] = useState(false)
  const [registerStep, setRegisterStep] = useState("form")
  const [verificationCode, setVerificationCode] = useState("")
  const [countdown, setCountdown] = useState(0)
  const [registerLoading, setRegisterLoading] = useState(false)
  const [verifyLoading, setVerifyLoading] = useState(false)
  const [registerForm, setRegisterForm] = useState({
    name: "",
    phone: "",
    email: "",
    password: "",
    confirmPassword: ""
  })
  const [registerError, setRegisterError] = useState("")
  const [registerSuccess, setRegisterSuccess] = useState("")
  const [showRegisterPassword, setShowRegisterPassword] = useState(false)
  const [showRegisterConfirmPassword, setShowRegisterConfirmPassword] = useState(false)

  const [showForgotPassword, setShowForgotPassword] = useState(false)
  const [forgotStep, setForgotStep] = useState("email")
  const [forgotForm, setForgotForm] = useState({
    email: "",
    code: "",
    newPassword: "",
    confirmPassword: ""
  })
  const [forgotDeliveryEmail, setForgotDeliveryEmail] = useState("")
  const [forgotCountdown, setForgotCountdown] = useState(0)
  const [forgotLoading, setForgotLoading] = useState(false)
  const [forgotVerifyLoading, setForgotVerifyLoading] = useState(false)
  const [forgotError, setForgotError] = useState("")
  const [forgotSuccess, setForgotSuccess] = useState("")
  const [showForgotNewPassword, setShowForgotNewPassword] = useState(false)
  const [showForgotConfirmPassword, setShowForgotConfirmPassword] = useState(false)

  useEffect(() => {
    if (registerStep !== "verify" || countdown <= 0) return undefined
    const timer = setInterval(() => {
      setCountdown((prev) => (prev > 0 ? prev - 1 : 0))
    }, 1000)
    return () => clearInterval(timer)
  }, [registerStep, countdown])

  useEffect(() => {
    if (forgotStep !== "reset" || forgotCountdown <= 0) return undefined
    const timer = setInterval(() => {
      setForgotCountdown((prev) => (prev > 0 ? prev - 1 : 0))
    }, 1000)
    return () => clearInterval(timer)
  }, [forgotStep, forgotCountdown])

  const formatCountdown = (totalSeconds) => {
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
  }

  const handleLogin = () => {
    if (typeof onPasswordLogin === "function") {
      onPasswordLogin(identifier, password, rememberMe)
    }
  }

  const openRegisterModal = () => {
    setShowRegister(true)
    setRegisterStep("form")
    setRegisterError("")
    setRegisterSuccess("")
    setVerificationCode("")
    setCountdown(0)
  }

  const closeRegisterModal = () => {
    setShowRegister(false)
    setRegisterStep("form")
    setRegisterError("")
    setRegisterSuccess("")
    setVerificationCode("")
    setCountdown(0)
  }

  const updateRegisterField = (field) => (e) => {
    if (field === "email" || field === "name" || field === "password" || field === "confirmPassword") {
      setRegisterError("")
    }
    setRegisterSuccess("")
    setRegisterForm((prev) => ({ ...prev, [field]: e.target.value }))
  }

  const handleRegisterSubmit = async (e) => {
    e.preventDefault()
    if (!registerForm.name.trim()) {
      setRegisterError("Họ và tên là bắt buộc")
      return
    }
    if (!registerForm.email.trim()) {
      setRegisterError("Email là bắt buộc")
      return
    }
    if (!registerForm.password.trim()) {
      setRegisterError("Mật khẩu là bắt buộc")
      return
    }
    if (registerForm.password !== registerForm.confirmPassword) {
      setRegisterError("Mật khẩu nhập lại không khớp")
      return
    }

    setRegisterLoading(true)
    setRegisterError("")
    setRegisterSuccess("")
    try {
      const res = await fetch(apiUrl("/api/xac-thuc/dang-ky/gui-ma"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: registerForm.name.trim(),
          phone: registerForm.phone.trim(),
          email: registerForm.email.trim(),
          password: registerForm.password,
          confirm_password: registerForm.confirmPassword
        })
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || "Không gửi được mã xác thực")
      }

      setVerificationCode("")
      setRegisterStep("verify")
      setCountdown(Number(data.expires_in_seconds || 300))
      setRegisterSuccess("Đã gửi mã xác thực tới email của bạn")
    } catch (err) {
      setRegisterError(err.message || "Không gửi được mã xác thực")
    } finally {
      setRegisterLoading(false)
    }
  }

  const backToRegisterForm = () => {
    setRegisterStep("form")
    setRegisterError("")
    setRegisterSuccess("")
    setVerificationCode("")
    setCountdown(0)
  }

  const handleResendVerification = async () => {
    setRegisterLoading(true)
    setVerificationCode("")
    setRegisterError("")
    setRegisterSuccess("")
    try {
      const res = await fetch(apiUrl("/api/xac-thuc/dang-ky/gui-ma"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: registerForm.name.trim(),
          phone: registerForm.phone.trim(),
          email: registerForm.email.trim(),
          password: registerForm.password,
          confirm_password: registerForm.confirmPassword
        })
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || "Không gửi lại được mã xác thực")
      }
      setCountdown(Number(data.expires_in_seconds || 300))
      setRegisterSuccess("Đã gửi lại mã xác thực tới email của bạn")
    } catch (err) {
      setRegisterError(err.message || "Không gửi lại được mã xác thực")
    } finally {
      setRegisterLoading(false)
    }
  }

  const handleVerifyRegistration = async () => {
    if (!verificationCode.trim()) {
      setRegisterError("Vui lòng nhập mã xác thực")
      return
    }

    setVerifyLoading(true)
    setRegisterError("")
    setRegisterSuccess("")
    try {
      const res = await fetch(apiUrl("/api/xac-thuc/dang-ky/xac-thuc"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: registerForm.email.trim(),
          code: verificationCode.trim()
        })
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || "Xác thực tài khoản thất bại")
      }

      setIdentifier(registerForm.email.trim())
      setVerificationCode("")
      setCountdown(0)
      setShowRegister(false)
    } catch (err) {
      setRegisterError(err.message || "Xác thực tài khoản thất bại")
    } finally {
      setVerifyLoading(false)
    }
  }

  const openForgotPasswordModal = () => {
    setShowForgotPassword(true)
    setForgotStep("email")
    setForgotForm({
      email: "",
      code: "",
      newPassword: "",
      confirmPassword: ""
    })
    setForgotDeliveryEmail("")
    setForgotCountdown(0)
    setForgotError("")
    setForgotSuccess("")
  }

  const closeForgotPasswordModal = () => {
    setShowForgotPassword(false)
    setForgotStep("email")
    setForgotCountdown(0)
    setForgotError("")
    setForgotSuccess("")
  }

  const updateForgotField = (field) => (e) => {
    setForgotError("")
    setForgotSuccess("")
    setForgotForm((prev) => ({ ...prev, [field]: e.target.value }))
  }

  const sendForgotPasswordCode = async () => {
    if (!forgotForm.email.trim()) {
      setForgotError("Vui lòng nhập email")
      return false
    }

    setForgotLoading(true)
    setForgotError("")
    setForgotSuccess("")
    try {
      const res = await fetch(apiUrl("/api/xac-thuc/quen-mat-khau/gui-ma"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: forgotForm.email.trim() })
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || "Không gửi được mã xác thực")
      }

      setForgotStep("reset")
      setForgotDeliveryEmail(data.delivery_email || forgotForm.email.trim())
      setForgotCountdown(Number(data.expires_in_seconds || 300))
      setForgotSuccess("Đã gửi mã xác thực tới email của bạn")
      return true
    } catch (err) {
      setForgotError(err.message || "Không gửi được mã xác thực")
      return false
    } finally {
      setForgotLoading(false)
    }
  }

  const handleForgotPasswordSubmit = async (e) => {
    e.preventDefault()
    await sendForgotPasswordCode()
  }

  const handleResetPassword = async () => {
    if (!forgotForm.newPassword.trim() || !forgotForm.confirmPassword.trim()) {
      setForgotError("Vui lòng nhập đủ mật khẩu mới và xác nhận mật khẩu")
      return
    }
    if (forgotForm.newPassword.trim().length < 6) {
      setForgotError("Mật khẩu mới phải từ 6 ký tự trở lên")
      return
    }
    if (forgotForm.newPassword !== forgotForm.confirmPassword) {
      setForgotError("Xác nhận mật khẩu không khớp")
      return
    }
    if (!forgotForm.code.trim()) {
      setForgotError("Vui lòng nhập mã xác thực")
      return
    }

    setForgotVerifyLoading(true)
    setForgotError("")
    setForgotSuccess("")
    try {
      const res = await fetch(apiUrl("/api/xac-thuc/quen-mat-khau/dat-lai"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: forgotForm.email.trim(),
          code: forgotForm.code.trim(),
          new_password: forgotForm.newPassword,
          confirm_password: forgotForm.confirmPassword
        })
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || "Đặt lại mật khẩu thất bại")
      }

      setIdentifier(forgotForm.email.trim())
      setPassword("")
      setShowLoginPassword(false)
      setForgotSuccess("Đặt lại mật khẩu thành công")
      setTimeout(() => {
        closeForgotPasswordModal()
      }, 500)
    } catch (err) {
      setForgotError(err.message || "Đặt lại mật khẩu thất bại")
    } finally {
      setForgotVerifyLoading(false)
    }
  }

  return (
    <div style={wrapper}>
      <style>{`
        .login-loading-spinner {
          width: 42px;
          height: 42px;
          border-radius: 999px;
          border: 4px solid rgba(255, 255, 255, 0.18);
          border-top-color: #ffffff;
          animation: login-spin 0.9s linear infinite;
        }

        @keyframes login-spin {
          to {
            transform: rotate(360deg);
          }
        }

        .login-input::placeholder {
          color: #6b7280;
        }

        .login-input,
        .login-input:-webkit-autofill,
        .login-input:-webkit-autofill:hover,
        .login-input:-webkit-autofill:focus {
          appearance: none;
          -webkit-appearance: none;
          color-scheme: light;
          background-color: #f3f4f6 !important;
          border-color: rgba(156, 163, 175, 0.45) !important;
          color: #4b5563 !important;
          -webkit-text-fill-color: #4b5563 !important;
          box-shadow: 0 0 0 1000px #f3f4f6 inset !important;
          caret-color: #4b5563;
          opacity: 1 !important;
          forced-color-adjust: none;
        }

        .login-password-toggle,
        .login-password-toggle svg {
          appearance: none;
          -webkit-appearance: none;
          color-scheme: light;
          background: transparent !important;
          color: #4b5563 !important;
          fill: #4b5563 !important;
          forced-color-adjust: none;
          -webkit-text-fill-color: #4b5563 !important;
          opacity: 1 !important;
        }

        .google-login-shell,
        .google-login-shell > div,
        .google-login-shell iframe {
          border-radius: 20px !important;
        }

        .google-login-shell {
          overflow: hidden;
        }
      `}</style>

      <div style={card}>
        <h1 style={title}>
          <img src="/logo.png" alt="Logo" style={{ height: 56, marginRight: 1 }} />
          TRỢ LÝ ẢO DOANH NGHIỆP
        </h1>

        <p style={subtitle}>Quản lý tài liệu và AI thông minh cho doanh nghiệp</p>

        {error && <div style={errorBox}>{error}</div>}

        <div style={form}>
          <label style={field}>
            <span style={label}>Email/Số điện thoại</span>
            <input
              className="login-input"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder="Nhập email hoặc số điện thoại"
              style={input}
            />
          </label>

          <label style={field}>
            <span style={label}>Mật khẩu</span>
            <div style={passwordFieldWrap}>
              <input
                className="login-input"
                type={showLoginPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleLogin() }}
                placeholder="Nhập mật khẩu"
                style={passwordInput}
              />
              <button
                type="button"
                onClick={() => setShowLoginPassword((prev) => !prev)}
                aria-label={showLoginPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
                className="login-password-toggle"
                style={togglePasswordButton}
              >
                {showLoginPassword ? <EyeInvisibleOutlined /> : <EyeOutlined />}
              </button>
            </div>
          </label>

          <label style={checkboxField}>
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
            />
            <span style={{ fontSize: 14, color: "#64748b" }}>
              Ghi nhớ đăng nhập
            </span>
          </label>

          <button
            type="button"
            onClick={handleLogin}
            disabled={loading}
            style={{
              ...primaryButton,
              opacity: loading ? 0.7 : 1,
              cursor: loading ? "not-allowed" : "pointer"
            }}
          >
            {loading ? "Đang đăng nhập..." : "Đăng nhập"}
          </button>

          <button type="button" onClick={openForgotPasswordModal} style={forgotButton}>
            Quên mật khẩu?
          </button>
        </div>

        <div style={divider}>
          <div style={dividerLine} />
          <span style={dividerText}>Hoặc</span>
          <div style={dividerLine} />
        </div>

        <div style={googleWrap} className="google-login-shell">
          <GoogleLogin
            onSuccess={onLogin}
            onError={() => {
              console.error("GoogleLogin frontend error")
            }}
            text="signin_with"
            theme="outline"
          />
        </div>

        <p style={helperText}>Vui lòng đăng nhập để tiếp tục</p>

        <button type="button" onClick={openRegisterModal} style={registerButton}>
          Đăng ký tài khoản
        </button>
      </div>

      {loading && (
        <div style={loadingOverlay}>
          <div style={loadingCard}>
            <div className="login-loading-spinner" />
            <div style={loadingTitle}>Đang đang nhập</div>
            <div style={loadingText}>Vui lòng chờ trong giây lát...</div>
          </div>
        </div>
      )}

      {showRegister && (
        <div style={modalBackdrop} onClick={closeRegisterModal}>
          <div style={modalCard} onClick={(e) => e.stopPropagation()}>
            <div style={modalHeader}>
              <div>
                <h2 style={modalTitle}>
                  {registerStep === "form" ? "Đăng ký tài khoản" : "Nhập mã xác thực"}
                </h2>
                <p style={modalSubtitle}>
                  {registerStep === "form"
                    ? "Nhập thông tin để tạo tài khoản mới"
                    : "Nhập mã đã gửi đến email của bạn trước khi hết hạn"}
                </p>
              </div>
            </div>

            {registerStep === "form" ? (
              <form style={modalForm} onSubmit={handleRegisterSubmit}>
                {registerError && <div style={errorBox}>{registerError}</div>}
                {registerSuccess && <div style={successBox}>{registerSuccess}</div>}

                <label style={field}>
                  <span style={label}>Họ và tên</span>
                  <input className="login-input" required value={registerForm.name} onChange={updateRegisterField("name")} placeholder="Nhập họ và tên" style={modalInput} />
                </label>

                <label style={field}>
                  <span style={label}>Số điện thoại</span>
                  <input className="login-input" value={registerForm.phone} onChange={updateRegisterField("phone")} placeholder="Nhập số điện thoại" style={modalInput} />
                </label>

                <label style={field}>
                  <span style={label}>Email</span>
                  <input className="login-input" required value={registerForm.email} onChange={updateRegisterField("email")} placeholder="Nhập email" style={modalInput} />
                </label>

                <label style={field}>
                  <span style={label}>Mật khẩu</span>
                  <div style={passwordFieldWrap}>
                    <input className="login-input" type={showRegisterPassword ? "text" : "password"} required value={registerForm.password} onChange={updateRegisterField("password")} placeholder="Tạo mật khẩu" style={passwordInput} />
                    <button type="button" onClick={() => setShowRegisterPassword((prev) => !prev)} aria-label={showRegisterPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} className="login-password-toggle" style={togglePasswordButton}>
                      {showRegisterPassword ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                    </button>
                  </div>
                </label>

                <label style={field}>
                  <span style={label}>Xác nhận mật khẩu</span>
                  <div style={passwordFieldWrap}>
                    <input className="login-input" type={showRegisterConfirmPassword ? "text" : "password"} required value={registerForm.confirmPassword} onChange={updateRegisterField("confirmPassword")} placeholder="Nhập lại mật khẩu" style={passwordInput} />
                    <button type="button" onClick={() => setShowRegisterConfirmPassword((prev) => !prev)} aria-label={showRegisterConfirmPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} className="login-password-toggle" style={togglePasswordButton}>
                      {showRegisterConfirmPassword ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                    </button>
                  </div>
                </label>

                <div style={modalActions}>
                  <button type="submit" disabled={registerLoading} style={{ ...primaryButton, opacity: registerLoading ? 0.7 : 1, cursor: registerLoading ? "not-allowed" : "pointer" }}>
                    {registerLoading ? "Đang gửi mã..." : "Tạo tài khoản"}
                  </button>
                </div>

                <button type="button" onClick={closeRegisterModal} style={backToLoginButton}>
                  Đã có tài khoản? Quay lại đăng nhập
                </button>
              </form>
            ) : (
              <div style={modalForm}>
                {registerError && <div style={errorBox}>{registerError}</div>}
                {registerSuccess && <div style={successBox}>{registerSuccess}</div>}

                <div style={verifyInfoBox}>
                  Mã xác thực đã được gửi tới: <strong>{registerForm.email}</strong>
                </div>

                <div style={countdownBox}>
                  {countdown > 0 ? `Mã hết hạn sau ${formatCountdown(countdown)}` : "Mã xác thực đã hết hạn"}
                </div>

                {countdown === 0 && (
                  <div style={expiredBox}>
                    <div>Mã xác thực đã hết hạn.</div>
                    <button type="button" onClick={handleResendVerification} disabled={registerLoading} style={{ ...resendButton, opacity: registerLoading ? 0.7 : 1, cursor: registerLoading ? "not-allowed" : "pointer" }}>
                      {registerLoading ? "Đang gửi..." : "Gửi lại"}
                    </button>
                  </div>
                )}

                <label style={field}>
                  <span style={label}>Mã xác thực</span>
                  <input className="login-input" value={verificationCode} onChange={(e) => setVerificationCode(e.target.value)} placeholder="Nhập mã 6 số" style={modalInput} disabled={countdown === 0} />
                </label>

                <div style={modalActions}>
                  <button type="button" onClick={backToRegisterForm} disabled={verifyLoading} style={{ ...secondaryActionButton, opacity: verifyLoading ? 0.7 : 1, cursor: verifyLoading ? "not-allowed" : "pointer" }}>
                    Quay lại
                  </button>
                  <button type="button" onClick={handleVerifyRegistration} disabled={countdown === 0 || verifyLoading} style={{ ...primaryButton, opacity: countdown === 0 || verifyLoading ? 0.6 : 1, cursor: countdown === 0 || verifyLoading ? "not-allowed" : "pointer" }}>
                    {verifyLoading ? "Đang xác thực..." : "Xác thực"}
                  </button>
                </div>

                <button type="button" onClick={closeRegisterModal} style={backToLoginButton}>
                  Đã có tài khoản? Quay lại đăng nhập
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {showForgotPassword && (
        <div style={modalBackdrop} onClick={closeForgotPasswordModal}>
          <div style={modalCard} onClick={(e) => e.stopPropagation()}>
            <div style={modalHeader}>
              <div>
                <h2 style={modalTitle}>{forgotStep === "email" ? "Quên mật khẩu" : "Đặt lại mật khẩu"}</h2>
                <p style={modalSubtitle}>
                  {forgotStep === "email"
                    ? "Nhập email để nhận mã xác thực"
                    : "Nhập mật khẩu mới, nhập lại mật khẩu mới và nhập mã xác thực"}
                </p>
              </div>
            </div>

            {forgotStep === "email" ? (
              <form style={modalForm} onSubmit={handleForgotPasswordSubmit}>
                {forgotError && <div style={errorBox}>{forgotError}</div>}
                {forgotSuccess && <div style={successBox}>{forgotSuccess}</div>}

                <label style={field}>
                  <span style={label}>Email</span>
                  <input className="login-input" required value={forgotForm.email} onChange={updateForgotField("email")} placeholder="Nhập email" style={modalInput} />
                </label>

                <div style={modalActions}>
                  <button type="submit" disabled={forgotLoading} style={{ ...primaryButton, opacity: forgotLoading ? 0.7 : 1, cursor: forgotLoading ? "not-allowed" : "pointer" }}>
                    {forgotLoading ? "Đang gửi mã..." : "Tiếp tục"}
                  </button>
                </div>
              </form>
            ) : (
              <div style={modalForm}>
                {forgotError && <div style={errorBox}>{forgotError}</div>}
                {forgotSuccess && <div style={successBox}>{forgotSuccess}</div>}

                <div style={verifyInfoBox}>
                  Mã xác thực đã được gửi tới: <strong>{forgotDeliveryEmail || forgotForm.email}</strong>
                </div>

                <label style={field}>
                  <span style={label}>Mật khẩu mới</span>
                  <div style={passwordFieldWrap}>
                    <input className="login-input" type={showForgotNewPassword ? "text" : "password"} value={forgotForm.newPassword} onChange={updateForgotField("newPassword")} placeholder="Nhập mật khẩu mới" style={passwordInput} />
                    <button type="button" onClick={() => setShowForgotNewPassword((prev) => !prev)} aria-label={showForgotNewPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} className="login-password-toggle" style={togglePasswordButton}>
                      {showForgotNewPassword ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                    </button>
                  </div>
                </label>

                <label style={field}>
                  <span style={label}>Nhập lại mật khẩu mới</span>
                  <div style={passwordFieldWrap}>
                    <input className="login-input" type={showForgotConfirmPassword ? "text" : "password"} value={forgotForm.confirmPassword} onChange={updateForgotField("confirmPassword")} placeholder="Nhập lại mật khẩu mới" style={passwordInput} />
                    <button type="button" onClick={() => setShowForgotConfirmPassword((prev) => !prev)} aria-label={showForgotConfirmPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} className="login-password-toggle" style={togglePasswordButton}>
                      {showForgotConfirmPassword ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                    </button>
                  </div>
                </label>

                <label style={field}>
                  <span style={label}>Mã xác thực</span>
                  <input className="login-input" value={forgotForm.code} onChange={updateForgotField("code")} placeholder="Nhập mã 6 số" style={modalInput} disabled={forgotCountdown === 0} />
                </label>

                <div style={countdownRow}>
                  <div style={countdownBox}>
                    {forgotCountdown > 0 ? `Mã hết hạn sau ${formatCountdown(forgotCountdown)}` : "Mã xác thực đã hết hạn"}
                  </div>
                  <button type="button" onClick={sendForgotPasswordCode} disabled={forgotLoading || forgotCountdown > 0} style={{ ...resendButton, opacity: forgotLoading || forgotCountdown > 0 ? 0.6 : 1, cursor: forgotLoading || forgotCountdown > 0 ? "not-allowed" : "pointer" }}>
                    {forgotLoading ? "Đang gửi..." : "Gửi lại"}
                  </button>
                </div>

                <div style={modalActions}>
                  <button type="button" onClick={() => setForgotStep("email")} disabled={forgotVerifyLoading} style={{ ...secondaryActionButton, opacity: forgotVerifyLoading ? 0.7 : 1, cursor: forgotVerifyLoading ? "not-allowed" : "pointer" }}>
                    Quay lại
                  </button>
                  <button type="button" onClick={handleResetPassword} disabled={forgotVerifyLoading || forgotCountdown === 0} style={{ ...primaryButton, opacity: forgotVerifyLoading || forgotCountdown === 0 ? 0.6 : 1, cursor: forgotVerifyLoading || forgotCountdown === 0 ? "not-allowed" : "pointer" }}>
                    {forgotVerifyLoading ? "Đang cập nhật..." : "Đặt lại mật khẩu"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

const wrapper = { minHeight: "100dvh", display: "flex", alignItems: "center", justifyContent: "center", padding: 20, background: "linear-gradient(160deg, #0f172a 0%, #102a43 48%, #f59e0b 160%)", color: "#0f172a" }
const card = { width: "100%", maxWidth: 460, padding: "clamp(24px, 5vw, 42px)", borderRadius: 28, background: "rgba(255, 255, 255, 0.92)", boxShadow: "0 30px 80px rgba(15, 23, 42, 0.3)", display: "flex", flexDirection: "column", color: "#0f172a", border: "1px solid rgba(255, 255, 255, 0.35)", backdropFilter: "blur(10px)" }
const title = { margin: "0 0 8px", width: "100%", textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center", gap: 1, paddingRight: 12, fontSize: 24, fontWeight: 700, color: "#0f172a", textTransform: "uppercase", letterSpacing: "0.04em" }
const subtitle = { opacity: 1, margin: "0 0 28px", textAlign: "center", fontSize: 14, lineHeight: 1.6, color: "#334155" }
const form = { display: "flex", flexDirection: "column", gap: 14 }
const field = { display: "flex", flexDirection: "column", gap: 8 }
const label = { fontSize: 14, fontWeight: 700, color: "#0f172a" }
const input = { width: "100%", height: 48, padding: "0 14px", borderRadius: 14, border: "1px solid rgba(156, 163, 175, 0.45)", outline: "none", fontSize: 16, boxSizing: "border-box", background: "#f3f4f6", color: "#4b5563", boxShadow: "inset 0 1px 2px rgba(15, 23, 42, 0.04)", colorScheme: "light", appearance: "none", WebkitAppearance: "none", WebkitTextFillColor: "#4b5563" }
const passwordFieldWrap = { position: "relative", display: "flex", alignItems: "center" }
const passwordInput = { ...input, paddingRight: 68 }
const togglePasswordButton = { position: "absolute", right: 12, border: "none", background: "transparent", color: "#4b5563", fontSize: 18, lineHeight: 1, cursor: "pointer", padding: 0, appearance: "none", WebkitAppearance: "none" }
const primaryButton = { height: 50, border: "none", borderRadius: 14, background: "linear-gradient(135deg, #0f172a 0%, #1d4ed8 100%)", color: "#fff", fontSize: 15, fontWeight: 700, marginTop: 4, padding: "0 18px", boxShadow: "0 16px 32px rgba(37, 99, 235, 0.22)" }
const forgotButton = { alignSelf: "flex-end", border: "none", background: "transparent", color: "#1d4ed8", fontSize: 14, fontWeight: 600, padding: 0, cursor: "pointer" }
const divider = { display: "flex", alignItems: "center", gap: 12, margin: "22px 0 16px" }
const dividerLine = { flex: 1, height: 1, background: "rgba(100, 116, 139, 0.25)" }
const dividerText = { fontSize: 13, color: "#64748b", whiteSpace: "nowrap" }
const googleWrap = { display: "flex", justifyContent: "center", borderRadius: 20, overflow: "hidden" }
const helperText = { fontSize: 12, textAlign: "center", margin: "18px 0 0", color: "#64748b" }
const registerButton = { border: "none", background: "transparent", color: "#0f172a", fontSize: 14, fontWeight: 700, padding: 0, marginTop: 20, alignSelf: "center", cursor: "pointer" }
const errorBox = { padding: 12, marginBottom: 16, borderRadius: 12, background: "#fee2e2", color: "#991b1b", fontSize: 13, textAlign: "center", border: "1px solid rgba(248, 113, 113, 0.35)" }
const successBox = { padding: 12, marginBottom: 16, borderRadius: 12, background: "#dcfce7", color: "#166534", fontSize: 13, textAlign: "center", border: "1px solid rgba(74, 222, 128, 0.35)" }
const loadingOverlay = { position: "fixed", inset: 0, background: "rgba(0, 0, 0, 0.72)", display: "flex", alignItems: "center", justifyContent: "center", padding: 16, zIndex: 1200 }
const loadingCard = { width: "100%", maxWidth: 360, borderRadius: 24, background: "rgba(255, 255, 255, 0.94)", border: "1px solid rgba(255, 255, 255, 0.2)", padding: 28, display: "flex", flexDirection: "column", alignItems: "center", gap: 14, textAlign: "center", boxShadow: "0 24px 70px rgba(15, 23, 42, 0.35)" }
const loadingTitle = { fontSize: 18, fontWeight: 800, color: "#0f172a" }
const loadingText = { fontSize: 14, lineHeight: 1.5, color: "#475569" }
const modalBackdrop = { position: "fixed", inset: 0, background: "rgba(0, 0, 0, 0.62)", display: "flex", alignItems: "center", justifyContent: "center", padding: 16, zIndex: 1000, overflowY: "auto" }
const modalCard = { width: "100%", maxWidth: 460, maxHeight: "calc(100dvh - 32px)", overflowY: "auto", background: "rgba(255, 255, 255, 0.96)", border: "1px solid rgba(255, 255, 255, 0.3)", borderRadius: 26, padding: "clamp(18px, 4vw, 28px)", boxShadow: "0 30px 80px rgba(15, 23, 42, 0.35)", color: "#0f172a" }
const modalHeader = { display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, marginBottom: 22 }
const modalTitle = { margin: 0, fontSize: 24, fontWeight: 800, color: "#0f172a" }
const modalSubtitle = { margin: "6px 0 0", fontSize: 14, color: "#64748b" }
const modalForm = { display: "flex", flexDirection: "column", gap: 14 }
const modalInput = { ...input }
const modalActions = { display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 8 }
const verifyInfoBox = { padding: 12, borderRadius: 14, background: "#eff6ff", fontSize: 14, lineHeight: 1.5, color: "#0f172a" }
const countdownBox = { textAlign: "center", fontSize: 14, fontWeight: 700, color: "#b45309" }
const countdownRow = { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }
const secondaryActionButton = { height: 48, borderRadius: 14, border: "1px solid rgba(148, 163, 184, 0.45)", background: "transparent", color: "#0f172a", padding: "0 18px", fontSize: 14, fontWeight: 700, cursor: "pointer" }
const backToLoginButton = { border: "none", background: "transparent", color: "#0f172a", fontSize: 14, fontWeight: 700, padding: 0, marginTop: 6, cursor: "pointer", alignSelf: "center" }
const expiredBox = { padding: 12, borderRadius: 12, background: "#fee2e2", color: "#991b1b", fontSize: 13, textAlign: "center", display: "flex", flexDirection: "column", gap: 10, alignItems: "center", border: "1px solid rgba(248, 113, 113, 0.35)" }
const resendButton = { border: "1px solid rgba(148, 163, 184, 0.45)", background: "transparent", color: "#0f172a", borderRadius: 12, padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }
const checkboxField = { display: "flex", alignItems: "center", gap: 8, marginTop: 4, color: "#0f172a" }
