// src/pages/DriveCallback.jsx
import { useEffect } from "react"
import { useNavigate } from "react-router-dom"
import { apiUrl } from "../utils/api"

export default function DriveCallback() {
  const navigate = useNavigate()

  useEffect(() => {
    console.log("🔄 DriveCallback component loaded")
    console.log("📍 Current URL:", window.location.href)
    
    const params = new URLSearchParams(window.location.search)
    const code = params.get("code")
    const error = params.get("error")
    const errorDesc = params.get("error_description")
    
    console.log("📦 URL params - code:", code, "error:", error, "description:", errorDesc)

    if (error) {
      console.error("❌ Google OAuth error:", error, errorDesc)
      alert(`Google lỗi: ${error} - ${errorDesc}`)
      navigate("/companies", { replace: true })
      return
    }

    if (!code) {
      console.warn("⚠️ Không nhận được code từ Google")
      alert("Không nhận được code từ Google")
      navigate("/companies", { replace: true })
      return
    }

    console.log("✅ Nhận được code từ Google")
    const redirectUri = `${window.location.origin}/drive-callback`

    // Prevent duplicate exchanges if the user refreshes or the component mounts twice
    const markerKey = `drive_code_processed_${code}`
    if (sessionStorage.getItem(markerKey) === "done") {
      console.log("⚠️ Code đã được xử lý trước đó, bỏ qua.")
      // Clean URL to avoid accidental re-submit on refresh
      try { window.history.replaceState({}, document.title, window.location.pathname) } catch {}
      navigate("/companies", { replace: true })
      return
    }
    
    // The backend `get_current_user` validates the Google access token, so
    // we must send a Google access token in the Authorization header.
    // Frontend stores the Google access token as `googleToken` (primary).
    // Keep fallbacks for older keys or session-stored company token.
    const jwt =
      localStorage.getItem("googleToken") ||
      localStorage.getItem("access_token") ||
      sessionStorage.getItem("companyToken") ||
      null

    console.log("🔐 Token found:", jwt ? "✅ có" : "❌ không có")

    if (!jwt) {
      alert("Phiên đăng nhập hết hạn hoặc thiếu token. Vui lòng đăng nhập lại.")
      navigate("/login", { replace: true })
      return
    }

    ;(async () => {
      try {
        // mark as pending to avoid race conditions
        try { sessionStorage.setItem(markerKey, "pending") } catch {}
        const res = await fetch(apiUrl("/api/companies/drive-exchange"), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${jwt}`,   // 👈 BẮT BUỘC
          },
          body: JSON.stringify({ code, redirect_uri: redirectUri }),
        })

        const data = await res.json()

        if (!res.ok) {
          console.error("Exchange failed:", data)
          alert(data?.detail || "Kết nối Google Drive thất bại")
        } else {
          console.log("💾 Exchange succeeded", data)
          alert("✅ Kết nối Google Drive thành công")
          // Force a full reload to let App and CompanyManagement pull fresh state
          try {
            window.location.replace('/admin/users')
            return
          } catch (e) {
            console.warn('window.location.replace failed, fallback to navigate')
          }
        }
      } catch (err) {
        console.error(err)
        alert("Lỗi khi kết nối Google Drive")
      } finally {
        try { sessionStorage.setItem(markerKey, "done") } catch {}
        // Remove code from URL so refresh won't resend it
        try { window.history.replaceState({}, document.title, window.location.pathname) } catch {}
        navigate("/admin/users", { replace: true })
      }
    })()
  }, [navigate])

  return <div>Đang kết nối Google Drive...</div>
}
