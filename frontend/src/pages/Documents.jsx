import { Table, Input, Select, Space, Button, Tag, Alert } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { useEffect, useState, useCallback } from 'react'
import { apiUrl } from '../utils/api'
import './Documents.css'

const { Option } = Select

const renderLoaiTaiLieu = (mimeType) => {
  if (!mimeType) return "Không xác định"
  if (mimeType.includes("pdf")) return "PDF"
  if (mimeType.includes("word") || mimeType.includes("officedocument.wordprocessingml")) return "Word"
  if (mimeType.includes("spreadsheet") || mimeType.includes("excel")) return "Excel"
  if (mimeType.includes("presentation") || mimeType.includes("powerpoint")) return "PowerPoint"
  if (mimeType.includes("image")) return "Hình ảnh"
  if (mimeType.includes("folder")) return "Thư mục"
  return "Khác"
}

const renderLoaiTag = (mimeType) => {
  const loai = renderLoaiTaiLieu(mimeType)
  let color = "default"

  if (loai === "PDF") color = "red"
  if (loai === "Word") color = "blue"
  if (loai === "Excel") color = "green"
  if (loai === "PowerPoint") color = "orange"
  if (loai === "Hình ảnh") color = "purple"

  return <Tag color={color}>{loai}</Tag>
}

export default function Documents({ accessToken, onAccountError }) {
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [keyword, setKeyword] = useState("")
  const [typeFilter, setTypeFilter] = useState("all") // all | pdf | word | excel | image
  const isLightTheme = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light'
  const pageTitleStyle = { color: isLightTheme ? '#1f2937' : '#fff', marginBottom: 18 }
  const toolbarStyle = isLightTheme
    ? {
        marginBottom: 18,
        flexWrap: 'wrap',
        padding: 14,
        borderRadius: 18,
        background: 'rgba(255, 252, 247, 0.94)',
        border: '1px solid #e6dfd2',
        boxShadow: '0 16px 40px rgba(15, 23, 42, 0.05)',
      }
    : { marginBottom: 16, flexWrap: 'wrap' }
  const emptyTextStyle = { color: isLightTheme ? '#6b7280' : '#fff', opacity: isLightTheme ? 1 : 0.7, marginTop: 14 }

  const columns = [
    {
      title: 'Tên file',
      dataIndex: 'name',
      ellipsis: true
    },
    {
      title: 'Loại',
      dataIndex: 'mimeType',
      width: 120,              // 👈 thu nhỏ cột loại
      align: 'center',
      render: (mimeType) => renderLoaiTag(mimeType)
    },
    {
      title: 'Mở',
      width: 90,
      align: 'center',
      render: (_, r) => (
        <a href={r.webViewLink} target="_blank" rel="noreferrer">
          Xem
        </a>
      )
    }
  ]

  const loadFiles = useCallback(async () => {
    if (!accessToken) {
      setError("Token không tồn tại - Vui lòng đăng nhập lại")
      return
    }

    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        apiUrl('/api/tai-lieu/danh-sach'),
        {
          headers: { Authorization: `Bearer ${accessToken}` },
          mode: 'cors'
        }
      )

      // Nếu 401 → tài khoản bị xoá hoặc quyền bị thu hồi
      if (res.status === 401) {
        const errorData = await res.json().catch(() => ({}))
        const errorMsg = errorData.detail || "Token không hợp lệ hoặc quyền bị thu hồi"
        setError(errorMsg)
        if (onAccountError) {
          onAccountError(errorMsg)
        }
        setFiles([])
        return
      }

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}))
        throw new Error(errorData.detail || `Lỗi ${res.status}`)
      }

      const data = await res.json()
      if (data.danh_sach && Array.isArray(data.danh_sach)) {
        setFiles(data.danh_sach)
        setError(null)
      } else {
        setFiles([])
        setError("Định dạng dữ liệu không hợp lệ")
      }
    } catch (e) {
      console.error("❌ Lỗi tải file:", e)
      
      let errorMsg = e.message || "Lỗi khi tải tài liệu"
      
      // Phát hiện network error
      if (e instanceof TypeError && e.message === "Failed to fetch") {
        errorMsg = "❌ Không thể kết nối tới server. Vui lòng kiểm tra:\n- Backend/API đã chạy chưa?\n- Kiểm tra cấu hình proxy hoặc kết nối mạng"
      }
      
      setError(errorMsg)
      setFiles([])
    } finally {
      setLoading(false)
    }
  }, [accessToken, onAccountError])

  useEffect(() => {
    loadFiles()
  }, [loadFiles])

  // 🔎 Lọc file theo tên + loại
  const filteredFiles = files.filter(file => {
    const matchName = file.name?.toLowerCase().includes(keyword.toLowerCase())

    let matchType = true
    if (typeFilter === "pdf") matchType = file.mimeType?.includes("pdf")
    if (typeFilter === "word") matchType = file.mimeType?.includes("word")
    if (typeFilter === "excel") matchType = file.mimeType?.includes("spreadsheet") || file.mimeType?.includes("excel")
    if (typeFilter === "image") matchType = file.mimeType?.includes("image")

    return matchName && matchType
  })

  return (
    <div>
      <h2 style={pageTitleStyle}>📁 Tài liệu</h2>

      {/* Error Alert */}
      {error && (
        <Alert
          message="⚠️ Lỗi khi tải tài liệu"
          description={
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>
              {error}
            </div>
          }
          type="error"
          showIcon
          closable
          onClose={() => setError(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      {/* Thanh lọc */}
      <Space style={toolbarStyle}>
        <Input
          placeholder="Tìm theo tên file..."
          prefix={<SearchOutlined />}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          allowClear
          style={{ width: 260 }}
        />

        <Select
          value={typeFilter}
          onChange={setTypeFilter}
          style={{ width: 160 }}
        >
          <Option value="all">Tất cả loại</Option>
          <Option value="pdf">PDF</Option>
          <Option value="word">Word</Option>
          <Option value="excel">Excel</Option>
          <Option value="image">Hình ảnh</Option>
        </Select>

        <Button icon={<ReloadOutlined />} onClick={loadFiles} loading={loading}>
          Làm mới
        </Button>
      </Space>

      <Table
        className="documents-table"
        rowKey="id"
        columns={columns}
        dataSource={filteredFiles}
        loading={loading}
        pagination={{ pageSize: 8 }}
      />

      {filteredFiles.length === 0 && !loading && (
        <p style={emptyTextStyle}>
          (Không có tài liệu phù hợp bộ lọc)
        </p>
      )}
    </div>
  )
}
