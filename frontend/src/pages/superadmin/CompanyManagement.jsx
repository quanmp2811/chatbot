import { Alert, Button, Input, Space, Table, message } from "antd"
import { googleLogout } from "@react-oauth/google"
import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { apiUrl } from "../../utils/api"
import "./CompanyManagement.css"

export default function CompanyManagement({ accessToken, user }) {
  const navigate = useNavigate()
  const COMPANY_POLL_INTERVAL_MS = 3000

  const [users, setUsers] = useState([])
  const [companyName, setCompanyName] = useState("")
  const [companyPlanName, setCompanyPlanName] = useState("Gói 0")
  const [companyRegisteredAt, setCompanyRegisteredAt] = useState(null)
  const [companyExpiresAt, setCompanyExpiresAt] = useState(null)
  const [email, setEmail] = useState("")
  const [position, setPosition] = useState("")
  const [isDriveConnected, setIsDriveConnected] = useState(false)
  const [driveTokenExpires, setDriveTokenExpires] = useState(null)
  const [hasRefreshToken, setHasRefreshToken] = useState(false)
  const [indexedDocuments, setIndexedDocuments] = useState(0)
  const [totalDocuments, setTotalDocuments] = useState(0)
  const [isSyncingDocuments, setIsSyncingDocuments] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState("")
  const [editPosition, setEditPosition] = useState("")
  const [error, setError] = useState(null)
  const [deleteCode, setDeleteCode] = useState("")
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteCodeLoading, setDeleteCodeLoading] = useState(false)
  const [deleteDeliveryEmail, setDeleteDeliveryEmail] = useState("")
  const isLightTheme = typeof document !== "undefined" && document.documentElement.dataset.theme === "light"
  const pageTitleStyle = { color: isLightTheme ? "#1f2937" : "#fff", marginBottom: 18 }
  const bodyTextStyle = { color: isLightTheme ? "#374151" : "#fff", marginBottom: 12 }
  const sectionTitleStyle = { color: isLightTheme ? "#1f2937" : "#fff", marginBottom: 14 }
  const subtleTextStyle = { marginLeft: 12, color: isLightTheme ? "#6b7280" : "#fff" }

  const redirectUri = `${window.location.origin}/drive-callback`

  const formatExpiry = (ts) => {
    if (!ts) return "--"
    try {
      return new Date(ts).toLocaleString("vi-VN")
    } catch {
      return ts
    }
  }

  const readResponsePayload = async (response) => {
    const raw = await response.text()
    try {
      return raw ? JSON.parse(raw) : {}
    } catch {
      return { raw_text: raw }
    }
  }

  const loadUsers = async () => {
    setError(null)
    try {
      const res = await fetch(apiUrl("/api/nguoi-dung/danh-sach"), {
        headers: { Authorization: `Bearer ${accessToken}` },
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || data.message || `Lỗi ${res.status}`)
      }
      setUsers(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(e.message || "Lỗi khi tải danh sách user")
      setUsers([])
    }
  }

  const loadCompany = async () => {
    const res = await fetch(apiUrl("/api/companies/me"), {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    if (!res.ok) return

    const data = await res.json()
    setCompanyName(data.name || "")
    setCompanyPlanName(data.current_plan_name || "Gói 0")
    setCompanyRegisteredAt(data.registered_at || null)
    setCompanyExpiresAt(data.expires_at || null)
    setIsDriveConnected(Boolean(data.has_drive_connected))
    setDriveTokenExpires(data.drive_token_expires || null)
    setHasRefreshToken(Boolean(data.has_refresh_token))

    const nextIndexedDocuments = Number(data.indexed_documents || 0)
    const nextTotalDocuments = Number(data.total_documents || 0)
    setIndexedDocuments(nextIndexedDocuments)
    setTotalDocuments(nextTotalDocuments)
    setIsSyncingDocuments(Boolean(data.has_drive_connected) && nextTotalDocuments > 0 && nextIndexedDocuments < nextTotalDocuments)
  }

  useEffect(() => {
    loadUsers()
    loadCompany()
  }, [accessToken])

  useEffect(() => {
    if (!accessToken || !isDriveConnected) return undefined
    const intervalId = window.setInterval(() => {
      loadCompany()
    }, COMPANY_POLL_INTERVAL_MS)
    return () => window.clearInterval(intervalId)
  }, [accessToken, isDriveConnected])

  const addUser = async () => {
    if (!email.trim()) {
      message.error("Nhập email trước đã")
      return
    }

    try {
      const res = await fetch(apiUrl("/api/nguoi-dung/them"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          email: email.toLowerCase().trim(),
          name: email.split("@")[0],
          position: position.trim(),
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || data.message || "Không thể thêm user")
      }
      if (data.user) {
        setUsers((prev) => [data.user, ...prev.filter((item) => item._id !== data.user._id)])
      } else {
        loadUsers()
      }
      setEmail("")
      setPosition("")
      message.success("Đã thêm user thành công")
    } catch (e) {
      message.error(e.message)
    }
  }

  const removeUser = async (userId) => {
    const ok = window.confirm("Bạn chắc chắn muốn xóa user này khỏi doanh nghiệp?")
    if (!ok) return

    try {
      const res = await fetch(apiUrl(`/api/nguoi-dung/${userId}`), {
        method: "DELETE",
        headers: { Authorization: `Bearer ${accessToken}` },
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || data.message || "Không thể xóa user")
      }
      message.success("Đã xóa user thành công")
      loadUsers()
    } catch (e) {
      message.error(e.message)
    }
  }

  const startInlineEdit = (record) => {
    setEditingId(record._id)
    setEditName(record.name || "")
    setEditPosition(record.position || "")
  }

  const cancelInlineEdit = () => {
    setEditingId(null)
    setEditName("")
    setEditPosition("")
  }

  const saveInlineEdit = async (userId) => {
    if (!editName.trim()) {
      message.error("Tên không được để trống")
      return
    }

    try {
      const res = await fetch(apiUrl(`/api/nguoi-dung/${userId}`), {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({ name: editName.trim(), position: editPosition.trim() }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || data.message || "Không thể cập nhật user")
      }
      if (data.user) {
        setUsers((prev) => prev.map((item) => (item._id === data.user._id ? data.user : item)))
      } else {
        loadUsers()
      }
      cancelInlineEdit()
      message.success("Đã cập nhật user")
    } catch (e) {
      message.error(e.message)
    }
  }

  const sendDeleteCompanyCode = async () => {
    setDeleteCodeLoading(true)
    try {
      const res = await fetch(apiUrl("/api/companies/delete/send-code"), {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      })
      const data = await readResponsePayload(res)
      if (!res.ok) {
        throw new Error(data.detail || data.raw_text || `HTTP ${res.status}`)
      }
      setDeleteDeliveryEmail(data.delivery_email || "")
      message.success(`Đã gửi mã xác thực${data.delivery_email ? ` tới ${data.delivery_email}` : ""}`)
    } catch (e) {
      message.error(e.message)
    } finally {
      setDeleteCodeLoading(false)
    }
  }

  const confirmDeleteCompany = async () => {
    const code = deleteCode.trim()
    if (!code) {
      message.error("Nhập mã xác thực trước đã")
      return
    }

    const ok = window.confirm(
      "Thao tác này sẽ xóa sạch dữ liệu doanh nghiệp, tài khoản thuộc doanh nghiệp và dữ liệu liên quan. Bạn chắc chắn muốn tiếp tục?",
    )
    if (!ok) return

    setDeleteLoading(true)
    try {
      const res = await fetch(apiUrl("/api/companies/delete/confirm"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({ code }),
      })
      const data = await readResponsePayload(res)
      if (!res.ok) {
        throw new Error(data.detail || data.raw_text || `HTTP ${res.status}`)
      }
      try {
        googleLogout()
      } catch {}
      localStorage.clear()
      sessionStorage.clear()
      message.success(data.message || "Đã xóa doanh nghiệp thành công")
      window.setTimeout(() => {
        window.location.href = "/login"
      }, 1200)
    } catch (e) {
      message.error(e.message)
    } finally {
      setDeleteLoading(false)
    }
  }

  const handleDriveConnect = async (reconnect = false) => {
    try {
      const res = await fetch(
        apiUrl(`/api/companies/drive/connect?redirect_uri=${encodeURIComponent(redirectUri)}`),
        {
          headers: { Authorization: `Bearer ${accessToken}` },
        },
      )
      const data = await readResponsePayload(res)
      if (!res.ok) {
        throw new Error(data.detail || data.raw_text || `HTTP ${res.status}`)
      }
      if (!data.url) {
        throw new Error("Không lấy được OAuth URL")
      }
      window.location.href = data.url
    } catch (e) {
      message.error(
        reconnect ? `Không thể kết nối lại token Drive: ${e.message}` : `Không thể kết nối Google Drive: ${e.message}`,
      )
    }
  }

  const handleRenewCompany = () => {
    const company = encodeURIComponent(companyName || "")
    navigate(`/company-payment?type=renew&company=${company}`)
  }

  const userColumns = [
    {
      title: "Tên",
      dataIndex: "name",
      render: (name, record) => {
        if (editingId === record._id) {
          return (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} />
              <Input value={editPosition} onChange={(e) => setEditPosition(e.target.value)} placeholder="Vị trí (tùy chọn)" />
            </div>
          )
        }
        const currentPosition = record.position || ""
        return currentPosition ? `${name || "Chưa cập nhật"} (${currentPosition})` : name || "Chưa cập nhật"
      },
    },
    { title: "Email", dataIndex: "email" },
    {
      title: "Vai trò",
      dataIndex: "role",
      width: 140,
      render: () => "User",
    },
    {
      title: "Hành động",
      width: 220,
      render: (_, record) =>
        editingId === record._id ? (
          <Space size="small">
            <Button size="small" type="primary" onClick={() => saveInlineEdit(record._id)}>
              Lưu
            </Button>
            <Button size="small" onClick={cancelInlineEdit}>
              Hủy
            </Button>
          </Space>
        ) : (
          <Space size="small">
            <Button size="small" onClick={() => startInlineEdit(record)}>
              Sửa
            </Button>
            <Button danger size="small" onClick={() => removeUser(record._id)}>
              Xóa
            </Button>
          </Space>
        ),
    },
  ]

  return (
    <div className="company-management">
      <h2 style={pageTitleStyle}>Quản lý doanh nghiệp</h2>

      <div className="company-management__subscription-card">
        <div className="company-management__subscription-grid">
          <div>
            <div className="company-management__subscription-label">Admin doanh nghiệp</div>
            <div className="company-management__subscription-value">{user?.name || user?.email || "Chưa cập nhật"}</div>
          </div>
          <div>
            <div className="company-management__subscription-label">Doanh nghiệp</div>
            <div className="company-management__subscription-value">{companyName || "Chưa cập nhật"}</div>
          </div>
          <div>
            <div className="company-management__subscription-label">Gói cước</div>
            <div className="company-management__subscription-value">{companyPlanName || "Gói 0"}</div>
          </div>
          <div>
            <div className="company-management__subscription-label">Ngày đăng ký</div>
            <div className="company-management__subscription-value">{formatExpiry(companyRegisteredAt)}</div>
          </div>
          <div>
            <div className="company-management__subscription-label">Ngày hết hạn</div>
            <div className="company-management__subscription-value">{formatExpiry(companyExpiresAt)}</div>
          </div>
        </div>
        <Button type="primary" onClick={handleRenewCompany}>
          Gia hạn
        </Button>
      </div>

      <p style={bodyTextStyle}>
        Kết nối Google Drive DN: <strong>{isDriveConnected ? "Đã kết nối" : "Chưa kết nối"}</strong>
      </p>

      <p style={bodyTextStyle}>
        Đã đồng bộ tài liệu:{" "}
        <strong>
          {indexedDocuments} / {totalDocuments}
        </strong>
      </p>

      {isSyncingDocuments ? <p style={{ ...bodyTextStyle, color: isLightTheme ? "#1d4ed8" : "#fff" }}>Đang đồng bộ tài liệu theo thời gian thực...</p> : null}

      {error ? (
        <Alert
          message="Lỗi tải dữ liệu"
          description={error}
          type="error"
          showIcon
          closable
          onClose={() => setError(null)}
          style={{ marginBottom: 16 }}
        />
      ) : null}

      {!isDriveConnected ? (
        <div style={{ marginBottom: 16 }}>
          <Button type="primary" onClick={() => handleDriveConnect(false)}>
            Kết nối Google Drive
          </Button>
        </div>
      ) : (
        <div style={{ marginBottom: 16 }}>
          <Button onClick={() => handleDriveConnect(true)}>Kết nối lại token</Button>
          {hasRefreshToken ? <span style={subtleTextStyle}>Có refresh token</span> : null}
        </div>
      )}

      {driveTokenExpires ? (
        <p style={bodyTextStyle}>
          Token Drive hết hạn: <strong>{formatExpiry(driveTokenExpires)}</strong>
        </p>
      ) : null}

      <div>
        <h3 style={sectionTitleStyle}>Quản lý user</h3>
        <div className="company-management__toolbar">
          <Input
            placeholder="Email user cần thêm"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="company-management__input company-management__input--email"
          />
          <Input
            placeholder="Vị trí làm việc (tùy chọn)"
            value={position}
            onChange={(e) => setPosition(e.target.value)}
            className="company-management__input company-management__input--position"
          />
          <Button type="primary" onClick={addUser}>
            Thêm user
          </Button>
        </div>

        <div className="company-management__table-wrap">
          <Table
            className="company-table"
            rowKey="_id"
            columns={userColumns}
            dataSource={users}
            pagination={{ pageSize: 8 }}
            scroll={{ x: 760 }}
          />
        </div>
      </div>

      <div className="company-management__danger-zone">
        <h3 className="company-management__danger-title">Xóa doanh nghiệp</h3>
        <p className="company-management__danger-text">
          Hành động này sẽ xóa sạch dữ liệu doanh nghiệp và gỡ doanh nghiệp khỏi mọi tài khoản liên quan.
        </p>

        <div className="company-management__danger-actions">
          <Button danger onClick={sendDeleteCompanyCode} loading={deleteCodeLoading}>
            Gửi mã xác thực
          </Button>
          <Input
            placeholder="Nhập mã xác thực xóa doanh nghiệp"
            value={deleteCode}
            onChange={(e) => setDeleteCode(e.target.value)}
            className="company-management__danger-input"
          />
          <Button danger type="primary" onClick={confirmDeleteCompany} loading={deleteLoading}>
            Xóa doanh nghiệp
          </Button>
        </div>

        {deleteDeliveryEmail ? (
          <p className="company-management__danger-hint">
            Mã xác thực đã được gửi tới: <strong>{deleteDeliveryEmail}</strong>
          </p>
        ) : null}
      </div>
    </div>
  )
}
