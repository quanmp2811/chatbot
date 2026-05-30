import { useEffect, useMemo, useState } from "react"
import {
  Button,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message,
} from "antd"
import { LogoutOutlined, MoreOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons"

import { apiUrl } from "../utils/api"

const { Title, Text } = Typography

async function readPayload(response) {
  const raw = await response.text()
  try {
    return raw ? JSON.parse(raw) : {}
  } catch {
    return { detail: raw }
  }
}

function formatDateTime(value) {
  if (!value) return "--"
  try {
    return new Date(value).toLocaleString("vi-VN")
  } catch {
    return value
  }
}

function formatCurrency(value) {
  const amount = Number(value || 0)
  if (!amount) return "0đ"
  return `${amount.toLocaleString("vi-VN")}đ`
}

export default function DeveloperCompanies({ accessToken, user, onLogout }) {
  const [managedCompanies, setManagedCompanies] = useState([])
  const [planOptions, setPlanOptions] = useState([])
  const [loading, setLoading] = useState(false)
  const [actionLoadingKey, setActionLoadingKey] = useState("")
  const [renewModalOpen, setRenewModalOpen] = useState(false)
  const [renewCompany, setRenewCompany] = useState(null)
  const [selectedPlanId, setSelectedPlanId] = useState("")
  const [planModalOpen, setPlanModalOpen] = useState(false)
  const [editingPlan, setEditingPlan] = useState(null)
  const [planForm] = Form.useForm()

  const loadData = async () => {
    setLoading(true)
    try {
      const res = await fetch(apiUrl("/api/developer/companies/overview"), {
        headers: { Authorization: `Bearer ${accessToken}` },
      })
      const data = await readPayload(res)
      if (!res.ok) {
        throw new Error(data.detail || "Không tải được dữ liệu")
      }

      setManagedCompanies(Array.isArray(data.managed_companies) ? data.managed_companies : [])
      const nextPlanOptions = Array.isArray(data.plan_options) ? data.plan_options : []
      setPlanOptions(nextPlanOptions)
      if (!selectedPlanId) {
        const firstPaidPlan = nextPlanOptions.find((item) => item.id !== "0")
        if (firstPaidPlan?.id) setSelectedPlanId(firstPaidPlan.id)
      }
    } catch (err) {
      message.error(err.message || "Không tải được dữ liệu")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [accessToken])

  const runAction = async (key, url, options = {}) => {
    setActionLoadingKey(key)
    try {
      const res = await fetch(apiUrl(url), {
        ...options,
        headers: {
          ...(options.headers || {}),
          Authorization: `Bearer ${accessToken}`,
        },
      })
      const data = await readPayload(res)
      if (!res.ok) {
        throw new Error(data.detail || "Thao tác thất bại")
      }
      message.success(data.message || "Đã cập nhật")
      await loadData()
      return true
    } catch (err) {
      message.error(err.message || "Thao tác thất bại")
      return false
    } finally {
      setActionLoadingKey("")
    }
  }

  const openRenewModal = (company) => {
    const firstPaidPlan = planOptions.find((item) => item.id !== "0")
    setRenewCompany(company)
    setSelectedPlanId(firstPaidPlan?.id || "")
    setRenewModalOpen(true)
  }

  const closeRenewModal = () => {
    if (actionLoadingKey.startsWith("renew-")) return
    setRenewModalOpen(false)
    setRenewCompany(null)
  }

  const submitRenew = async () => {
    if (!renewCompany?._id || !selectedPlanId) {
      message.error("Vui lòng chọn gói cước")
      return
    }

    const success = await runAction(
      `renew-${renewCompany._id}`,
      `/api/developer/companies/${renewCompany._id}/renew`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: selectedPlanId }),
      },
    )

    if (success) {
      closeRenewModal()
    }
  }

  const confirmRenew = () => {
    if (!renewCompany?._id || !selectedPlanId) {
      message.error("Vui lòng chọn gói cước")
      return
    }

    const selectedPlan = paidPlanOptions.find((plan) => plan.id === selectedPlanId)
    if (!selectedPlan) {
      message.error("Không tìm thấy gói cước đã chọn")
      return
    }

    Modal.confirm({
      title: `Xác nhận gia hạn cho ${renewCompany.name}?`,
      content: (
        <div style={{ display: "grid", gap: 8 }}>
          <Text>
            Doanh nghiệp sẽ được gia hạn với <strong>{selectedPlan.name}</strong>.
          </Text>
          <Text type="secondary">
            Giá gói: {formatCurrency(selectedPlan.price)} • Thời hạn: {selectedPlan.total_months} tháng
          </Text>
          <Text type="secondary">
            Hệ thống sẽ cộng dồn từ ngày hết hạn hiện tại nếu doanh nghiệp vẫn còn hạn.
          </Text>
        </div>
      ),
      okText: "Xác nhận gia hạn",
      cancelText: "Quay lại",
      onOk: submitRenew,
    })
  }

  const confirmExpire = (record) => {
    Modal.confirm({
      title: `Chuyển doanh nghiệp ${record.name} sang trạng thái hết hạn?`,
      content: `Thao tác này sẽ đặt gói cước của doanh nghiệp ${record.name} về Gói 0 và xóa ngày hết hạn.`,
      okText: "Hết hạn",
      cancelText: "Hủy",
      onOk: () =>
        runAction(`expiry-${record._id}`, `/api/developer/companies/${record._id}/toggle-expiry`, {
          method: "POST",
        }),
    })
  }

  const confirmDelete = (record) => {
    Modal.confirm({
      title: `Xóa doanh nghiệp ${record.name}?`,
      content: `Toàn bộ dữ liệu liên quan của doanh nghiệp ${record.name} sẽ bị xóa.`,
      okText: "Xóa",
      cancelText: "Hủy",
      okButtonProps: { danger: true },
      onOk: () => runAction(`delete-${record._id}`, `/api/developer/companies/${record._id}`, { method: "DELETE" }),
    })
  }

  const confirmToggleBlock = (record) => {
    const isBlocked = Boolean(record.is_blocked)
    Modal.confirm({
      title: isBlocked ? `Mở khóa doanh nghiệp ${record.name}?` : `Khóa doanh nghiệp ${record.name}?`,
      content: isBlocked
        ? `Doanh nghiệp ${record.name} sẽ được mở khóa và có thể tiếp tục sử dụng hệ thống nếu còn hiệu lực.`
        : `Doanh nghiệp ${record.name} sẽ bị khóa và không thể tiếp tục sử dụng hệ thống cho đến khi được mở khóa.`,
      okText: isBlocked ? "Mở khóa" : "Khóa",
      cancelText: "Hủy",
      okButtonProps: isBlocked ? {} : { danger: true },
      onOk: () =>
        runAction(`block-${record._id}`, `/api/developer/companies/${record._id}/toggle-block`, {
          method: "POST",
        }),
    })
  }

  const openCreatePlanModal = () => {
    setEditingPlan(null)
    planForm.setFieldsValue({
      id: "",
      name: "",
      duration_months: 1,
      bonus_months: 0,
      price: 0,
    })
    setPlanModalOpen(true)
  }

  const openEditPlanModal = (plan) => {
    setEditingPlan(plan)
    planForm.setFieldsValue({
      id: plan.id,
      name: plan.name,
      duration_months: Number(plan.duration_months || 0),
      bonus_months: Number(plan.bonus_months || 0),
      price: Number(plan.price || 0),
    })
    setPlanModalOpen(true)
  }

  const closePlanModal = () => {
    if (actionLoadingKey === "plan-submit") return
    setPlanModalOpen(false)
    setEditingPlan(null)
    planForm.resetFields()
  }

  const submitPlan = async () => {
    try {
      const values = await planForm.validateFields()
      const payload = {
        ...(editingPlan ? {} : { id: String(values.id || "").trim().toLowerCase() }),
        name: String(values.name || "").trim(),
        duration_months: Number(values.duration_months || 0),
        bonus_months: Number(values.bonus_months || 0),
        price: Number(values.price || 0),
      }

      const targetUrl = editingPlan
        ? `/api/developer/plans/${editingPlan.id}`
        : "/api/developer/plans"
      const method = editingPlan ? "PUT" : "POST"
      const success = await runAction("plan-submit", targetUrl, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })

      if (success) {
        closePlanModal()
      }
    } catch (err) {
      if (err?.errorFields) return
      message.error(err.message || "Không lưu được gói cước")
    }
  }

  const confirmDeletePlan = (plan) => {
    Modal.confirm({
      title: `Xóa gói cước ${plan.name}?`,
      content: `Gói cước ${plan.name} sẽ bị xóa khỏi danh sách và không còn dùng để gia hạn mới.`,
      okText: "Xóa",
      cancelText: "Hủy",
      okButtonProps: { danger: true },
      onOk: () => runAction(`plan-delete-${plan.id}`, `/api/developer/plans/${plan.id}`, { method: "DELETE" }),
    })
  }

  const paidPlanOptions = useMemo(
    () => planOptions.filter((plan) => plan.id !== "0"),
    [planOptions],
  )

  const planSelectOptions = useMemo(
    () =>
      paidPlanOptions.map((plan) => ({
        label: `${plan.name} • ${formatCurrency(plan.price)} • ${plan.total_months} tháng`,
        value: plan.id,
      })),
    [paidPlanOptions],
  )

  const managedColumns = [
    { title: "Tên doanh nghiệp", dataIndex: "name" },
    { title: "Người đại diện", dataIndex: "owner_name" },
    { title: "Địa chỉ email", dataIndex: "contact_email" },
    {
      title: "Gói cước",
      dataIndex: "current_plan_name",
      render: (_, record) => (
        <div>
          <div>{record.current_plan_name || "Gói 0"}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {formatCurrency(record.current_plan_price)}
          </Text>
        </div>
      ),
    },
    {
      title: "Ngày đăng ký",
      dataIndex: "registered_at",
      render: (value, record) => formatDateTime(value || record.created_at),
    },
    {
      title: "Ngày hết hạn",
      dataIndex: "expires_at",
      render: (value) => formatDateTime(value),
    },
    {
      title: "",
      key: "actions",
      width: 72,
      align: "center",
      render: (_, record) => {
        const menuItems = [
          {
            key: "renew",
            label: record.is_expired ? "Gia hạn gói cước" : "Gia hạn gói cước",
          },
          ...(!record.is_expired
            ? [
                {
                  key: "expire",
                  label: "Chuyển hết hạn",
                },
              ]
            : []),
          {
            key: "block",
            label: record.is_blocked ? "Mở khoá doanh nghiệp" : "Khóa doanh nghiệp",
          },
          {
            key: "delete",
            label: <span style={{ color: "#dc2626" }}>Xóa doanh nghiệp</span>,
          },
        ]

        return (
          <Dropdown
            trigger={["click"]}
            menu={{
              items: menuItems,
              onClick: ({ key }) => {
                if (key === "renew") {
                  openRenewModal(record)
                  return
                }
                if (key === "expire") {
                  confirmExpire(record)
                  return
                }
                if (key === "block") {
                  confirmToggleBlock(record)
                  return
                }
                if (key === "delete") {
                  confirmDelete(record)
                }
              },
            }}
          >
            <Button
              type="text"
              icon={<MoreOutlined style={{ fontSize: 18 }} />}
              loading={
                actionLoadingKey === `renew-${record._id}` ||
                actionLoadingKey === `expiry-${record._id}` ||
                actionLoadingKey === `block-${record._id}` ||
                actionLoadingKey === `delete-${record._id}`
              }
            />
          </Dropdown>
        )
      },
    },
  ]

  const planColumns = [
    {
      title: "Mã gói",
      dataIndex: "id",
      width: 120,
      render: (value) => <Text code>{value}</Text>,
    },
    { title: "Tên gói cước", dataIndex: "name" },
    {
      title: "Thời hạn",
      key: "duration",
      render: (_, record) => (
        <div>
          <div>{record.duration_months} tháng chính</div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Tặng {record.bonus_months} tháng • Tổng {record.total_months} tháng
          </Text>
        </div>
      ),
    },
    {
      title: "Giá",
      dataIndex: "price",
      width: 160,
      render: (value) => formatCurrency(value),
    },
    {
      title: "",
      key: "actions",
      width: 200,
      render: (_, record) => {
        const isDefaultPlan = record.id === "0"
        return (
          <Space>
            <Button onClick={() => openEditPlanModal(record)} disabled={isDefaultPlan}>
              Sửa
            </Button>
            <Button danger onClick={() => confirmDeletePlan(record)} disabled={isDefaultPlan}>
              Xóa
            </Button>
          </Space>
        )
      },
    },
  ]

  const totalMonths = Form.useWatch("duration_months", planForm) || 0
  const bonusMonths = Form.useWatch("bonus_months", planForm) || 0

  return (
    <div style={page}>
      <div style={header}>
        <div>
          <Text style={eyebrow}>Bảng điều khiển nhà phát triển</Text>
          <Title level={2} style={{ color: "#f8fafc", marginTop: 8, marginBottom: 8 }}>
            Quản lý các doanh nghiệp
          </Title>
          <Text style={{ color: "#cbd5e1" }}>Đăng nhập bằng: {user?.email || "developer"}</Text>
        </div>

        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadData} loading={loading}>
            Làm mới
          </Button>
          <Button icon={<LogoutOutlined />} onClick={onLogout}>
            Đăng xuất
          </Button>
        </Space>
      </div>

      <section style={sectionCard}>
        <div style={sectionHeader}>
          <div>
            <Title level={4} style={sectionTitle}>
              Quản lý gói cước
            </Title>
            <Text type="secondary">Thêm, sửa, xóa gói cước dùng cho toàn hệ thống.</Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreatePlanModal}>
            Thêm gói cước
          </Button>
        </div>
        <Table
          rowKey="id"
          loading={loading}
          columns={planColumns}
          dataSource={planOptions}
          pagination={false}
          locale={{ emptyText: "Chưa có gói cước nào" }}
          scroll={{ x: 960 }}
        />
      </section>

      <section style={sectionCard}>
        <Title level={4} style={sectionTitle}>
          Quản lý các doanh nghiệp
        </Title>
        <Table
          rowKey="_id"
          loading={loading}
          columns={managedColumns}
          dataSource={managedCompanies}
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
          locale={{ emptyText: "Chưa có doanh nghiệp nào" }}
          scroll={{ x: 1240 }}
        />
      </section>

      <Modal
        title={renewCompany ? `Gia hạn cho ${renewCompany.name}` : "Gia hạn doanh nghiệp"}
        open={renewModalOpen}
        onCancel={closeRenewModal}
        onOk={confirmRenew}
        okText="Gia hạn"
        cancelText="Hủy"
        confirmLoading={renewCompany ? actionLoadingKey === `renew-${renewCompany._id}` : false}
      >
        <div style={{ display: "grid", gap: 12 }}>
          <Text>Chọn gói cước để gia hạn. Hệ thống sẽ cộng dồn vào ngày hết hạn hiện tại nếu doanh nghiệp vẫn còn hạn.</Text>
          <Select
            value={selectedPlanId || undefined}
            onChange={setSelectedPlanId}
            options={planSelectOptions}
            placeholder="Chọn gói cước"
          />
        </div>
      </Modal>

      <Modal
        title={editingPlan ? `Sửa gói cước ${editingPlan.name}` : "Thêm gói cước"}
        open={planModalOpen}
        onCancel={closePlanModal}
        onOk={submitPlan}
        okText={editingPlan ? "Lưu thay đổi" : "Thêm gói cước"}
        cancelText="Hủy"
        confirmLoading={actionLoadingKey === "plan-submit"}
      >
        <Form form={planForm} layout="vertical">
          <Form.Item
            name="id"
            label="Mã gói"
            rules={[
              { required: true, message: "Vui lòng nhập mã gói cước" },
              {
                validator: (_, value) => {
                  if (!value) return Promise.resolve()
                  if (/\s/.test(String(value))) {
                    return Promise.reject(new Error("Mã gói cước không được chứa khoảng trắng"))
                  }
                  return Promise.resolve()
                },
              },
            ]}
          >
            <Input disabled={Boolean(editingPlan)} placeholder="Ví dụ: 3m" />
          </Form.Item>

          <Form.Item
            name="name"
            label="Tên gói cước"
            rules={[{ required: true, message: "Vui lòng nhập tên gói cước" }]}
          >
            <Input placeholder="Ví dụ: Gói 3 tháng" />
          </Form.Item>

          <Form.Item
            name="duration_months"
            label="Số tháng chính"
            rules={[{ required: true, message: "Vui lòng nhập số tháng chính" }]}
          >
            <InputNumber min={0} style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item
            name="bonus_months"
            label="Số tháng tặng"
            rules={[{ required: true, message: "Vui lòng nhập số tháng tặng" }]}
          >
            <InputNumber min={0} style={{ width: "100%" }} />
          </Form.Item>

          <Form.Item
            name="price"
            label="Giá"
            rules={[{ required: true, message: "Vui lòng nhập giá gói cước" }]}
          >
            <InputNumber min={0} style={{ width: "100%" }} />
          </Form.Item>

          <div style={planHint}>
            Tổng thời gian sử dụng: <strong>{Number(totalMonths || 0) + Number(bonusMonths || 0)} tháng</strong>
          </div>
        </Form>
      </Modal>
    </div>
  )
}

const page = {
  minHeight: "100dvh",
  padding: 24,
  background: "linear-gradient(180deg, #07111f 0%, #0f172a 45%, #12263f 100%)",
}

const header = {
  maxWidth: 1320,
  margin: "0 auto 24px",
  display: "flex",
  justifyContent: "space-between",
  gap: 16,
  alignItems: "flex-start",
  flexWrap: "wrap",
}

const sectionCard = {
  maxWidth: 1320,
  margin: "0 auto 24px",
  padding: 20,
  borderRadius: 24,
  background: "rgba(255, 255, 255, 0.95)",
  boxShadow: "0 24px 80px rgba(2, 6, 23, 0.28)",
}

const sectionHeader = {
  display: "flex",
  justifyContent: "space-between",
  gap: 16,
  alignItems: "center",
  flexWrap: "wrap",
  marginBottom: 18,
}

const sectionTitle = {
  marginTop: 0,
  marginBottom: 8,
}

const eyebrow = {
  color: "#7dd3fc",
  textTransform: "uppercase",
  letterSpacing: "0.12em",
  fontWeight: 700,
  fontSize: 12,
}

const planHint = {
  borderRadius: 14,
  padding: "12px 14px",
  background: "#eff6ff",
  color: "#0f172a",
}
