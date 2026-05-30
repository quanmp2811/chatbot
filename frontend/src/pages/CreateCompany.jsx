import { useState } from "react"
import { useNavigate } from "react-router-dom"

export default function CreateCompany({ onSubmit, loading, onBackToLogin, canUseTrial = true }) {
  const navigate = useNavigate()
  const [error, setError] = useState("")
  const [activeAction, setActiveAction] = useState("")

  const handleBack = (e) => {
    try {
      e?.preventDefault()
    } catch {}

    if (typeof onBackToLogin === "function") {
      onBackToLogin()
      return
    }

    try {
      navigate("/login")
    } catch {
      window.location.href = "/login"
    }
  }

  const handleCreate = async (e, creationMode) => {
    e.preventDefault()
    setError("")
    setActiveAction(creationMode)

    try {
      const formElement = e.currentTarget instanceof HTMLFormElement ? e.currentTarget : e.currentTarget?.form
      if (!formElement) {
        throw new Error("Không thể đọc thông tin doanh nghiệp")
      }

      const formData = new FormData(formElement)
      const ownerName = String(formData.get("owner_name") || "").trim()
      if (/\d/.test(ownerName)) {
        throw new Error("Họ tên người đại diện không được chứa số")
      }

      const result = await onSubmit?.(formData, { creationMode })
      if (result?.handled_navigation) {
        return
      }
      const companyName = encodeURIComponent(String(formData.get("name") || "").trim())

      if (creationMode === "trial") {
        navigate("/chat", { replace: true })
        return
      }

      const message = encodeURIComponent(result?.message || "Đăng ký doanh nghiệp thành công")
      navigate(`/company-payment?type=renew&message=${message}&company=${companyName}`)
    } catch (err) {
      setError(err.message || "Không thể tạo doanh nghiệp")
      setActiveAction("")
    }
  }

  return (
    <div style={wrapper}>
      <div style={card}>
        <h2 style={title}>Tạo doanh nghiệp mới</h2>
        <p style={subtitle}>Chào mừng đến với trợ lý ảo doanh nghiệp</p>

        {error ? <div style={errorBox}>{error}</div> : null}

        <form onSubmit={(e) => handleCreate(e, "paid")} style={form}>
          <input name="name" placeholder="Tên doanh nghiệp" required style={input} />
          <input name="owner_name" placeholder="Họ tên người đại diện" required style={input} pattern="^(?!.*\\d).+$" />

          {canUseTrial ? (
            <div style={noticeBox}>
              <strong style={noticeTitle}>Dùng thử 7 ngày</strong>
              <div style={noticeLine}>1GB bộ nhớ lưu trữ tài liệu</div>
              <div style={noticeLine}>Tối đa 5 tài khoản trong doanh nghiệp</div>
            </div>
          ) : null}

          <div
            style={{
              ...actionRow,
              gridTemplateColumns: canUseTrial ? "repeat(2, minmax(0, 1fr))" : "1fr",
            }}
          >
            {canUseTrial ? (
              <button
                type="button"
                disabled={loading}
                onClick={(e) => handleCreate(e, "trial")}
                style={{
                  ...trialBtn,
                  opacity: loading ? (activeAction === "trial" ? 1 : 0.4) : 1,
                  cursor: loading ? "not-allowed" : "pointer",
                }}
              >
                {loading && activeAction === "trial" ? "Đang xử lý..." : "Dùng thử 7 ngày"}
              </button>
            ) : null}

            <button
              type="submit"
              disabled={loading}
              style={{
                ...submitBtn,
                opacity: loading ? (activeAction === "paid" ? 1 : 0.4) : 1,
                cursor: loading ? "not-allowed" : "pointer",
              }}
            >
              {loading && activeAction === "paid" ? "Đang xử lý..." : "Thanh toán"}
            </button>
          </div>

          <button type="button" onClick={handleBack} style={backBtn}>
            Quay lại đăng nhập
          </button>
        </form>
      </div>
    </div>
  )
}

const wrapper = {
  minHeight: "100dvh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 20,
  background: "radial-gradient(circle at top, #16324f 0%, #0b1220 52%, #05070d 100%)",
}

const card = {
  width: "100%",
  maxWidth: 460,
  padding: 28,
  borderRadius: 22,
  background: "rgba(8, 17, 31, 0.88)",
  border: "1px solid rgba(148, 163, 184, 0.22)",
  boxShadow: "0 30px 80px rgba(0, 0, 0, 0.35)",
  color: "#f8fafc",
}

const title = {
  margin: "0 0 8px",
  textAlign: "center",
}

const subtitle = {
  margin: "0 0 24px",
  textAlign: "center",
  color: "#cbd5e1",
  lineHeight: 1.6,
}

const form = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
}

const actionRow = {
  display: "grid",
  gap: 12,
}

const input = {
  width: "100%",
  padding: "12px 14px",
  borderRadius: 12,
  border: "1px solid rgba(148, 163, 184, 0.3)",
  outline: "none",
  fontSize: 15,
  boxSizing: "border-box",
  background: "rgba(15, 23, 42, 0.9)",
  color: "#f8fafc",
}

const submitBtn = {
  padding: "12px 14px",
  borderRadius: 12,
  border: "none",
  background: "linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%)",
  color: "#fff",
  fontSize: 15,
  fontWeight: 600,
}

const trialBtn = {
  padding: "12px 14px",
  borderRadius: 12,
  border: "1px solid rgba(125, 211, 252, 0.35)",
  background: "linear-gradient(135deg, rgba(14, 165, 233, 0.16) 0%, rgba(34, 197, 94, 0.16) 100%)",
  color: "#e0f2fe",
  fontSize: 15,
  fontWeight: 600,
}

const backBtn = {
  padding: "12px 14px",
  borderRadius: 12,
  border: "1px solid rgba(148, 163, 184, 0.3)",
  background: "transparent",
  color: "#e2e8f0",
  fontSize: 14,
}

const errorBox = {
  marginBottom: 14,
  borderRadius: 12,
  padding: "10px 12px",
  background: "rgba(220, 38, 38, 0.16)",
  border: "1px solid rgba(248, 113, 113, 0.35)",
  color: "#fecaca",
}

const noticeBox = {
  borderRadius: 16,
  padding: "14px 16px",
  background: "rgba(15, 23, 42, 0.72)",
  border: "1px solid rgba(148, 163, 184, 0.2)",
  color: "#cbd5e1",
}

const noticeTitle = {
  display: "block",
  marginBottom: 8,
  color: "#f8fafc",
}

const noticeLine = {
  fontSize: 14,
  lineHeight: 1.6,
}
