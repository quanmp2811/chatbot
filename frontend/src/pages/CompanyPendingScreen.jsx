import { useMemo, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"

const COPY_BY_STATE = {
  blocked: {
    title: "Doanh nghiệp đang bị khóa",
    description: "Doanh nghiệp của bạn hiện đang bị khóa. Vui lòng liên hệ nhà phát triển để được hỗ trợ.",
  },
  expired: {
    title: "Doanh nghiệp chưa thanh toán",
    description:
      "Doanh nghiệp của bạn chưa hoàn tất thanh toán. Bạn hãy thanh toán để tiếp tục sử dụng dịch vụ hoặc kích hoạt dùng thử 7 ngày cho tài khoản đăng lần đầu.",
  },
  inactive: {
    title: "Doanh nghiệp chưa kích hoạt",
    description:
      "Doanh nghiệp của bạn chưa được kích hoạt sử dụng. Vui lòng tiếp tục thanh toán hoặc dùng thử 7 ngày nếu tài khoản vẫn còn đủ điều kiện.",
  },
}

const SUPPORT_LINKS_BY_STATE = {
  blocked: "https://www.facebook.com/quanmp2811/",
  expired: "https://www.facebook.com/quanmp2811/",
  inactive: "https://www.facebook.com/quanmp2811/",
}

export default function CompanyPendingScreen({ user, onLogout, onActivateTrial, loading = false }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [trialError, setTrialError] = useState("")
  const [trialSubmitting, setTrialSubmitting] = useState(false)

  const accessState = user?.company_access_state || "inactive"
  const content = useMemo(() => COPY_BY_STATE[accessState] || COPY_BY_STATE.inactive, [accessState])
  const supportLink = SUPPORT_LINKS_BY_STATE[accessState]
  const showRenewButton = accessState === "expired" || accessState === "inactive"
  const showTrialButton = Boolean(user?.can_use_trial) && (accessState === "expired" || accessState === "inactive")
  const search = useMemo(() => new URLSearchParams(location.search), [location.search])
  const paymentStatus = search.get("payment") || ""
  const paymentMethod = search.get("method") || ""
  const showPaymentSuccess = paymentStatus === "success"

  const handleRenew = () => {
    const company = encodeURIComponent(user?.company_name || "")
    navigate(`/company-payment?type=renew&company=${company}`)
  }

  const handleActivateTrial = async () => {
    if (!onActivateTrial || trialSubmitting || loading) return
    setTrialError("")
    setTrialSubmitting(true)
    try {
      await onActivateTrial()
      navigate("/chat")
    } catch (error) {
      setTrialError(error?.message || "Không thể kích hoạt dùng thử 7 ngày")
    } finally {
      setTrialSubmitting(false)
    }
  }

  return (
    <div style={wrapper}>
      <div style={card}>
        <div style={badge}>Trạng thái doanh nghiệp</div>
        <h1 style={title}>{content.title}</h1>
        <p style={description}>{content.description}</p>

        <div style={detailGrid}>
          <div style={detailBox}>
            <span style={detailLabel}>Doanh nghiệp</span>
            <strong>{user?.company_name || "Chưa cập nhật"}</strong>
          </div>
          <div style={detailBox}>
            <span style={detailLabel}>Email tài khoản</span>
            <strong>{user?.email || "Chưa cập nhật"}</strong>
          </div>
        </div>

        {showPaymentSuccess ? (
          <div style={successBox}>
            Hệ thống đã ghi nhận thanh toán {paymentMethod === "atm" ? "ATM " : ""}thành công. Nếu doanh nghiệp chưa được mở ngay, vui lòng chờ trong giây lát để trạng thái được cập nhật.
          </div>
        ) : null}

        {trialError ? <div style={errorBox}>{trialError}</div> : null}

        <div style={actions}>
          <button type="button" onClick={onLogout} style={button} disabled={trialSubmitting || loading}>
            Về trang đăng nhập
          </button>
          {showTrialButton ? (
            <button
              type="button"
              onClick={handleActivateTrial}
              style={trialButton}
              disabled={trialSubmitting || loading}
            >
              {trialSubmitting ? "Đang kích hoạt..." : "Dùng thử 7 ngày"}
            </button>
          ) : null}
          {showRenewButton ? (
            <button type="button" onClick={handleRenew} style={renewButton} disabled={trialSubmitting || loading}>
              Thanh toán ngay
            </button>
          ) : null}
        </div>
      </div>

      {supportLink ? (
        <a
          href={supportLink}
          target="_blank"
          rel="noreferrer"
          aria-label="Liên hệ hỗ trợ"
          title="Liên hệ hỗ trợ"
          style={floatingContactButton}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" style={floatingIcon}>
            <path
              fill="currentColor"
              d="M12 2C6.477 2 2 6.145 2 11.257c0 2.914 1.454 5.514 3.726 7.203V22l3.308-1.816c.883.245 1.814.373 2.966.373 5.523 0 10-4.145 10-9.257S17.523 2 12 2zm1.004 12.445-2.547-2.72-4.973 2.72 5.47-5.808 2.617 2.72 4.903-2.72-5.47 5.808z"
            />
          </svg>
        </a>
      ) : null}
    </div>
  )
}

const wrapper = {
  minHeight: "100dvh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 20,
  background: "linear-gradient(160deg, #0f172a 0%, #102a43 48%, #f59e0b 160%)",
  position: "relative",
}

const card = {
  width: "100%",
  maxWidth: 680,
  borderRadius: 28,
  padding: 32,
  background: "rgba(255, 255, 255, 0.92)",
  boxShadow: "0 30px 80px rgba(15, 23, 42, 0.3)",
  color: "#0f172a",
}

const badge = {
  display: "inline-flex",
  padding: "8px 14px",
  borderRadius: 999,
  background: "#dbeafe",
  color: "#1d4ed8",
  fontSize: 13,
  fontWeight: 700,
  marginBottom: 18,
}

const title = {
  margin: "0 0 12px",
  fontSize: "clamp(28px, 5vw, 40px)",
}

const description = {
  margin: "0 0 24px",
  fontSize: 16,
  lineHeight: 1.7,
  color: "#334155",
}

const detailGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 14,
  marginBottom: 24,
}

const detailBox = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 18,
  borderRadius: 18,
  background: "#eff6ff",
}

const detailLabel = {
  fontSize: 12,
  textTransform: "uppercase",
  letterSpacing: "0.08em",
  color: "#64748b",
}

const actions = {
  display: "flex",
  gap: 12,
  flexWrap: "wrap",
}

const button = {
  border: "none",
  borderRadius: 14,
  padding: "12px 18px",
  background: "#0f172a",
  color: "#fff",
  fontSize: 15,
  fontWeight: 700,
  cursor: "pointer",
}

const trialButton = {
  border: "none",
  borderRadius: 14,
  padding: "12px 18px",
  background: "#2563eb",
  color: "#fff",
  fontSize: 15,
  fontWeight: 800,
  cursor: "pointer",
}

const renewButton = {
  border: "none",
  borderRadius: 14,
  padding: "12px 18px",
  background: "#f59e0b",
  color: "#0f172a",
  fontSize: 15,
  fontWeight: 800,
  cursor: "pointer",
}

const errorBox = {
  marginBottom: 16,
  borderRadius: 14,
  padding: "12px 14px",
  background: "#fef2f2",
  border: "1px solid #fecaca",
  color: "#b91c1c",
  lineHeight: 1.6,
}

const successBox = {
  marginBottom: 16,
  borderRadius: 14,
  padding: "12px 14px",
  background: "#ecfdf5",
  border: "1px solid #86efac",
  color: "#166534",
  lineHeight: 1.6,
}

const floatingContactButton = {
  position: "fixed",
  right: 24,
  bottom: 24,
  width: 58,
  height: 58,
  borderRadius: "50%",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  background: "#1877f2",
  color: "#fff",
  boxShadow: "0 16px 36px rgba(24, 119, 242, 0.38)",
  textDecoration: "none",
  zIndex: 20,
}

const floatingIcon = {
  width: 28,
  height: 28,
}
