import { Table, Tag, Button, Input, Space, Alert } from 'antd'
import { useEffect, useState } from 'react'
import { apiUrl } from '../../utils/api'
import './UserManagement.css'

export default function UserManagement({ accessToken }) {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [email, setEmail] = useState("")
  const [position, setPosition] = useState("")
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState("")
  const [editPosition, setEditPosition] = useState("")

  const loadUsers = async () => {
    if (!accessToken) {
      setError("Token không tồn tại - Vui lòng đăng nhập lại")
      return
    }

    setLoading(true)
    setError(null)
    try {
      const res = await fetch(apiUrl("/api/nguoi-dung/danh-sach"), {
        headers: { Authorization: `Bearer ${accessToken}` }
      })

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}))
        throw new Error(errorData.detail || `Lỗi ${res.status}`)
      }

      const data = await res.json()
      setUsers(Array.isArray(data) ? data : [])
    } catch (e) {
      console.error("❌ Lỗi tải danh sách user:", e)
      setError(e.message || "Lỗi khi tải danh sách user")
      setUsers([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadUsers()
  }, [accessToken])

  const addUser = async () => {
    if (!email) return alert("Nhập email trước đã")

    try {
      const res = await fetch(apiUrl("/api/nguoi-dung/them"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`
        },
        body: JSON.stringify({ 
          email: email.toLowerCase().trim(),
          name: email.split("@")[0],
          position: position.trim()
        })
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        alert("Lỗi: " + (data.detail || data.message || "Không biết lỗi gì"))
        return
      }

      if (data.user) {
        setUsers(prev => [data.user, ...prev.filter(u => u._id !== data.user._id)])
      } else {
        loadUsers()
      }

      alert("Đã thêm user thành công")
      setEmail("")
      setPosition("")
    } catch (e) {
      alert("Lỗi: " + e.message)
    }
  }

  const removeUser = async (userId) => {
    const ok = window.confirm("Bạn chắc chắn muốn xoá user này khỏi công ty?")
    if (!ok) return

    try {
      const res = await fetch(apiUrl(`/api/nguoi-dung/${userId}`), {
        method: "DELETE",
        headers: { Authorization: `Bearer ${accessToken}` }
      })

      const data = await res.json()
      if (!res.ok) {
        alert("Lỗi: " + (data.detail || data.message || "Không thể xoá user"))
        return
      }

      alert("Đã xoá user thành công")
      loadUsers()
    } catch (e) {
      alert("Lỗi: " + e.message)
    }
  }

  const startInlineEdit = (user) => {
    setEditingId(user._id)
    setEditName(user.name || "")
    setEditPosition(user.position || "")
  }

  const cancelInlineEdit = () => {
    setEditingId(null)
    setEditName("")
    setEditPosition("")
  }

  const saveInlineEdit = async (userId) => {
    if (!editName.trim()) return alert('Tên không được để trống')
    try {
      const res = await fetch(apiUrl(`/api/nguoi-dung/${userId}`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify({ name: editName.trim(), position: editPosition.trim() })
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) { alert('Lỗi: ' + (data.detail || data.message || 'Không biết lỗi')); return }
      if (data.user) {
        setUsers(prev => [data.user, ...prev.filter(u => u._id !== data.user._id)])
      } else {
        loadUsers()
      }
      cancelInlineEdit()
      alert('Đã cập nhật user')
    } catch (e) { alert('Lỗi: ' + e.message) }
  }

  const columns = [
    { 
      title: 'Tên',
      dataIndex: 'name',
      ellipsis: true,
      render: (name, record) => {
        if (editingId === record._id) {
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} />
              <Input value={editPosition} onChange={(e) => setEditPosition(e.target.value)} placeholder="Vị trí (tùy chọn)" />
            </div>
          )
        }
        const position = record.position || ''
        return position ? `${name} (${position})` : name
      }
    },
    { 
      title: 'Email', 
      dataIndex: 'email',
      ellipsis: true
    },
    { 
      title: 'Vai trò', 
      dataIndex: 'role',
      width: 100,
      render: (role) => <Tag color="blue">User</Tag>
    },
    {
      title: 'Hành động',
      width: 140,
      render: (_, record) => (
        editingId === record._id ? (
          <Space size="small">
            <Button size="small" type="primary" onClick={() => saveInlineEdit(record._id)}>Lưu</Button>
            <Button size="small" onClick={cancelInlineEdit}>Hủy</Button>
          </Space>
        ) : (
          <Space size="small">
            <Button size="small" onClick={() => startInlineEdit(record)}>✏️ Sửa</Button>
            <Button danger size="small" onClick={() => removeUser(record._id)}>Xoá</Button>
          </Space>
        )
      )
    }
  ]

  return (
    <div className="user-management">
      <h2 style={{ color: "#fff" }}>👥 Quản lý người dùng</h2>

      {error && (
        <Alert
          message="⚠️ Lỗi"
          description={error}
          type="error"
          showIcon
          closable
          onClose={() => setError(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      <div style={{ marginBottom: 16 }}>
        <h3 style={{ color: "#fff" }}>➕ Thêm user mới</h3>
        <Space className="user-management__toolbar" style={{ marginBottom: 12, flexWrap: 'wrap' }}>
          <Input
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="user-management__input"
          />
          <Input
            placeholder="Vị trí (tùy chọn)"
            value={position}
            onChange={(e) => setPosition(e.target.value)}
            className="user-management__input"
          />
          <Button type="primary" onClick={addUser}>
            ➕ Thêm User
          </Button>
        </Space>
      </div>

      <div className="user-management__table-wrap">
        <Table
          className="users-table"
          rowKey="_id"
          columns={columns}
          dataSource={users}
          loading={loading}
          pagination={{ pageSize: 10 }}
          scroll={{ x: 720 }}
        />
      </div>

      {users.length === 0 && !loading && (
        <p style={{ opacity: 0.7, color: "#fff" }}>(Chưa có user thường nào trong công ty)</p>
      )}
    </div>
  )
}
