import { useEffect, useMemo, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"
import { QRCode } from "antd"
import { apiUrl } from "../utils/api"
const assetUrl = (f) => `${import.meta.env.BASE_URL || "/"}${f}`.replace("//", "/")
const PLANS = [
  { id: "1m", name: "Gói 1 tháng", price: "150.000đ", months: "1 tháng", bonus: "Không có ưu đãi thêm", amount: 150000, accent: "#2563eb" },
  { id: "6m", name: "Gói 6 tháng", price: "900.000đ", months: "6 tháng", bonus: "Ưu đãi tặng thêm 1 tháng", amount: 900000, accent: "#ea580c" },
  { id: "12m", name: "Gói 12 tháng", price: "1.800.000đ", months: "12 tháng", bonus: "Ưu đãi tặng thêm 3 tháng", amount: 1800000, accent: "#059669" },
]
const METHODS = [
  { id: "atm", name: "Thẻ ATM", description: "Thanh toán qua thẻ ngân hàng ATM.", logoSrc: assetUrl("atm.png") },
  { id: "vnpay", name: "VNPay", description: "Thanh toán nhanh qua cổng VNPay.", logoSrc: assetUrl("logo-vnpay.png") },
  { id: "momo", name: "MoMo", description: "Thanh toán bằng ví điện tử MoMo.", logoSrc: assetUrl("momo-logo.png") },
]
const VN_BANK_OPTIONS = [
  "Vietcombank",
  "BIDV",
  "VietinBank",
  "Agribank",
  "Techcombank",
  "MB Bank",
  "ACB",
  "VPBank",
  "TPBank",
  "Sacombank",
  "HDBank",
  "VIB",
  "SHB",
  "OCB",
  "SeABank",
  "MSB",
  "Eximbank",
  "Nam A Bank",
]
const BANK_CARD_LENGTHS = {
  Vietcombank: [19],
  VIB: [19],
}
async function readPayload(response) { const raw = await response.text(); try { return raw ? JSON.parse(raw) : {} } catch { return { detail: raw } } }
function normalizeCardHolderName(value) { return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").replace(/[^A-Za-z\s]/g, "").replace(/\s+/g, " ").trimStart().toUpperCase() }
function normalizeIssueMonth(value) { return String(Math.min(Math.max(Number(value), 1), 12)).padStart(2, "0") }
function normalizeIssueDate(value) {
  const rawValue = String(value || "").replace(/[^\d/]/g, "")
  if (!rawValue) return ""
  const slashIndex = rawValue.indexOf("/")

  if (slashIndex >= 0) {
    const rawMonthPart = rawValue.slice(0, slashIndex).replace(/\D/g, "").slice(0, 2)
    const rawYearPart = rawValue.slice(slashIndex + 1).replace(/\D/g, "").slice(0, 2)

    if (!rawMonthPart) return `/${rawYearPart}`
    if (rawMonthPart.length === 1) {
      const month = rawValue.endsWith("/") && !rawYearPart ? rawMonthPart.padStart(2, "0") : rawMonthPart
      return `${month}/${rawYearPart}`
    }

    return `${normalizeIssueMonth(rawMonthPart)}/${rawYearPart}`
  }

  const digits = rawValue.replace(/\D/g, "").slice(0, 4)
  if (digits.length <= 1) return digits
  const month = normalizeIssueMonth(digits.slice(0, 2))
  const year = digits.slice(2)
  return year ? `${month}/${year}` : month
}
function finalizeIssueDate(value) {
  const rawValue = String(value || "").replace(/[^\d/]/g, "")
  if (!rawValue) return ""
  const slashIndex = rawValue.indexOf("/")
  if (slashIndex < 0) return rawValue
  const rawMonthPart = rawValue.slice(0, slashIndex).replace(/\D/g, "").slice(0, 2)
  const rawYearPart = rawValue.slice(slashIndex + 1).replace(/\D/g, "").slice(0, 2)
  if (!rawMonthPart) return rawYearPart ? `/${rawYearPart}` : ""
  const month = rawMonthPart.length === 1 ? rawMonthPart.padStart(2, "0") : normalizeIssueMonth(rawMonthPart)
  return `${month}/${rawYearPart}`
}
function getValidCardLengths(bankName) { return BANK_CARD_LENGTHS[bankName] || [16] }
function isIssueDateNotInFuture(value) {
  if (!/^\d{2}\/\d{2}$/.test(value)) return false
  const [monthText, yearText] = value.split("/")
  const month = Number(monthText)
  const year = 2000 + Number(yearText)
  if (!month || month < 1 || month > 12) return false
  const now = new Date()
  const currentMonth = now.getMonth() + 1
  const currentYear = now.getFullYear()
  return year < currentYear || (year === currentYear && month <= currentMonth)
}
export default function CompanyPaymentPage({ user, accessToken, onPaymentSuccess }) {
  const navigate = useNavigate(); const location = useLocation(); const [selectedPlan, setSelectedPlan] = useState("1m"); const [selectedMethod, setSelectedMethod] = useState("vnpay"); const [submitting, setSubmitting] = useState(false); const [confirmingAtmPayment, setConfirmingAtmPayment] = useState(false); const [confirmingMockPayment, setConfirmingMockPayment] = useState(false); const [error, setError] = useState(""); const [mockPaymentUrl, setMockPaymentUrl] = useState(""); const [mockPaymentToken, setMockPaymentToken] = useState(""); const [mockPaymentState, setMockPaymentState] = useState("idle"); const [mockScanCount, setMockScanCount] = useState(0); const [atmForm, setAtmForm] = useState({ bankName: "", cardHolder: "", cardNumber: "", issueDate: "" }); const [showSuccessDialog, setShowSuccessDialog] = useState(false); const [successCountdown, setSuccessCountdown] = useState(15); const [successRedirectPath, setSuccessRedirectPath] = useState(""); const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth <= 900)
  const search = useMemo(() => new URLSearchParams(location.search), [location.search]); const paymentType = search.get("type") === "renew" ? "renew" : "register"; const isZeroTestMode = search.get("test_zero") === "1"; const mockZeroStatus = search.get("mock_zero") || ""; const mockTokenFromQuery = search.get("mock_token") || ""; const atmStatus = search.get("atm_status") || ""; const atmToken = search.get("atm_token") || ""; const isAtmConfirmView = atmStatus === "confirm"; const vnpayStatus = search.get("vnpay_status") === "success" ? "success" : search.get("vnpay_status") === "failed" ? "failed" : ""; const companyName = search.get("company") || user?.company_name || "Doanh nghiệp của bạn"
  const selectedPlanDetail = PLANS.find((p) => p.id === selectedPlan) || PLANS[0]; const effectiveAmount = isZeroTestMode ? 0 : selectedPlanDetail.amount; const normalizedAtmCardNumber = atmForm.cardNumber.replace(/\D/g, ""); const validCardLengths = getValidCardLengths(atmForm.bankName); const isAtmCardLengthValid = validCardLengths.includes(normalizedAtmCardNumber.length); const atmCardLengthHint = atmForm.bankName ? `Ngân hàng ${atmForm.bankName} yêu cầu số thẻ ${validCardLengths.join(" hoặc ")} số.` : ""; const atmCardLengthError = normalizedAtmCardNumber && atmForm.bankName && !isAtmCardLengthValid ? atmCardLengthHint : ""; const isIssueDateComplete = /^\d{2}\/\d{2}$/.test(atmForm.issueDate); const isIssueDateValid = isIssueDateComplete && isIssueDateNotInFuture(atmForm.issueDate); const issueDateError = atmForm.issueDate && !isIssueDateComplete ? "Ngày phát hành phải đủ tháng và năm theo dạng MM/YY." : atmForm.issueDate && !isIssueDateValid ? "Ngày phát hành phải nhỏ hơn hoặc bằng tháng hiện tại." : ""; const ctaText = submitting ? "Đang chuyển đến cổng thanh toán..." : "Thanh toán"; const isCompanyActive = user?.company_access_state === "active" && !user?.company_is_expired; const displayCtaText = submitting ? (isZeroTestMode ? "Đang tạo QR test..." : "Đang chuyển đến cổng thanh toán...") : (isZeroTestMode ? "Tạo QR thanh toán 0đ" : ctaText); const isAtmFormComplete = atmForm.bankName.trim() && atmForm.cardHolder.trim() && isAtmCardLengthValid && isIssueDateValid; const shouldKeepPaymentScreen = paymentType === "renew" || isAtmConfirmView || atmStatus === "success" || vnpayStatus === "success" || vnpayStatus === "failed" || mockZeroStatus === "paid" || mockZeroStatus === "scanned" || Boolean(mockTokenFromQuery)
  useEffect(() => { if (typeof window === "undefined") return undefined; const updateViewport = () => setIsMobile(window.innerWidth <= 900); updateViewport(); window.addEventListener("resize", updateViewport); return () => window.removeEventListener("resize", updateViewport) }, [])
  useEffect(() => { setMockPaymentUrl(""); setMockPaymentToken(""); setMockPaymentState("idle"); setMockScanCount(0) }, [selectedMethod, selectedPlan])
  useEffect(() => { if (!isZeroTestMode) return; if (!mockTokenFromQuery) { if (mockZeroStatus === "paid") setMockPaymentState("paid"); return } setMockPaymentToken(mockTokenFromQuery); if (mockZeroStatus === "scanned") { setMockPaymentState("scanned"); return } if (mockZeroStatus === "paid") setMockPaymentState("paid") }, [isZeroTestMode, mockTokenFromQuery, mockZeroStatus])
  useEffect(() => { if (!mockPaymentToken || mockPaymentUrl) return; setMockPaymentUrl(apiUrl(`/api/payments/mock/landing?token=${encodeURIComponent(mockPaymentToken)}`)) }, [mockPaymentToken, mockPaymentUrl])
  useEffect(() => { if (!isCompanyActive || shouldKeepPaymentScreen) return; navigate("/chat", { replace: true }) }, [isCompanyActive, navigate, shouldKeepPaymentScreen])
  useEffect(() => { if (!showSuccessDialog || !successRedirectPath) return undefined; if (successCountdown <= 0) { navigate(successRedirectPath, { replace: true }); return undefined } const timer = window.setTimeout(() => setSuccessCountdown((current) => current - 1), 1000); return () => window.clearTimeout(timer) }, [navigate, showSuccessDialog, successCountdown, successRedirectPath])
  useEffect(() => { if (showSuccessDialog) return; let method = ""; if (atmStatus === "success") method = "atm"; else if (vnpayStatus === "success") method = "vnpay"; else if (mockPaymentState === "paid" || mockZeroStatus === "paid") method = "mock"; if (!method) return; const redirectPath = isCompanyActive ? "/chat" : `/company-status?payment=success&method=${method}`; setSuccessRedirectPath(redirectPath); setSuccessCountdown(15); setShowSuccessDialog(true) }, [showSuccessDialog, atmStatus, vnpayStatus, mockPaymentState, mockZeroStatus, isCompanyActive])
  useEffect(() => { if (!isZeroTestMode || !mockPaymentToken || mockPaymentState === "paid") return undefined; let cancelled = false; const pollStatus = async () => { try { const res = await fetch(apiUrl(`/api/payments/mock/status?token=${encodeURIComponent(mockPaymentToken)}`)); const data = await readPayload(res); if (!res.ok || cancelled) return; setMockPaymentState(data.status || "pending"); setMockScanCount(Number(data.scan_count || 0)) } catch { if (!cancelled) setMockPaymentState((c) => c || "pending") } }; pollStatus(); const timer = window.setInterval(pollStatus, 2500); return () => { cancelled = true; window.clearInterval(timer) } }, [isZeroTestMode, mockPaymentToken, mockPaymentState])
  const handleConfirmMockPayment = async () => { if (!mockPaymentToken || confirmingMockPayment) return; setConfirmingMockPayment(true); setError(""); try { const res = await fetch(apiUrl("/api/payments/mock/confirm"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: mockPaymentToken }) }); const data = await readPayload(res); if (!res.ok) throw new Error(data.detail || "Không thể xác nhận thanh toán demo"); setMockPaymentState(data.status || "paid") } catch (err) { setError(err.message || "Không thể xác nhận thanh toán demo") } finally { setConfirmingMockPayment(false) } }
  const handleConfirmAtmPayment = async () => { if (!atmToken || confirmingAtmPayment) return; setConfirmingAtmPayment(true); setError(""); try { const res = await fetch(apiUrl("/api/payments/atm/confirm"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: atmToken }) }); const data = await readPayload(res); if (!res.ok) throw new Error(data.detail || "Không thể xác nhận thanh toán ATM"); let refreshedUser = null; if (typeof onPaymentSuccess === "function") { try { refreshedUser = await onPaymentSuccess() } catch { refreshedUser = null } } const nextUser = refreshedUser || user; const isActivated = nextUser?.company_access_state === "active" && !nextUser?.company_is_expired; setSuccessRedirectPath(isActivated ? "/chat" : "/company-status?payment=success&method=atm"); setSuccessCountdown(15); setShowSuccessDialog(true) } catch (err) { setError(err.message || "Không thể xác nhận thanh toán ATM") } finally { setConfirmingAtmPayment(false) } }
  const handleIssueDateChange = (event) => { const input = event.target; const nextValue = input.value; const selectionStart = input.selectionStart ?? nextValue.length; const normalizedValue = normalizeIssueDate(nextValue); const normalizedPrefix = normalizeIssueDate(nextValue.slice(0, selectionStart)); setAtmForm((current) => ({ ...current, issueDate: normalizedValue })); window.requestAnimationFrame(() => { const nextCaret = Math.min(normalizedPrefix.length, normalizedValue.length); input.setSelectionRange(nextCaret, nextCaret) }) }
  const handleIssueDateBlur = (event) => { const finalizedValue = finalizeIssueDate(event.target.value); setAtmForm((current) => ({ ...current, issueDate: finalizedValue })) }
  const handleAtmFieldChange = (field) => (event) => { const nextValue = event.target.value; const formattedValue = field === "cardNumber" ? nextValue.replace(/\D/g, "").slice(0, 19).replace(/(\d{4})(?=\d)/g, "$1 ").trim() : field === "cardHolder" ? normalizeCardHolderName(nextValue) : nextValue; setAtmForm((current) => ({ ...current, [field]: formattedValue })) }
  const handleCloseSuccessDialog = () => { if (!successRedirectPath) return; setShowSuccessDialog(false); navigate(successRedirectPath, { replace: true }) }
  const successDialog = showSuccessDialog ? <div style={successModalOverlay}><div style={successModalCard}><div style={successModalTitle}>Thanh toán thành công</div><div style={successModalText}>Hệ thống sẽ tự chuyển trang sau <strong>{successCountdown}</strong> giây.</div><button type="button" onClick={handleCloseSuccessDialog} style={successModalButton}>OK</button></div></div> : null
  const handlePay = async () => { if (submitting) return; setError(""); setMockPaymentUrl(""); if (!accessToken) { setError("Thiếu phiên đăng nhập. Vui lòng đăng nhập lại."); return } setSubmitting(true); try { const orderDesc = paymentType === "renew" ? `Gia hạn ${selectedPlanDetail.name} cho ${companyName}` : `Đăng ký ${selectedPlanDetail.name} cho ${companyName}`; const endpoint = selectedMethod === "atm" ? "/api/payments/atm/create" : isZeroTestMode ? "/api/payments/mock/create" : selectedMethod === "momo" ? "/api/payments/momo/create" : "/api/payments/vnpay/create"; const res = await fetch(apiUrl(endpoint), { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` }, body: JSON.stringify({ amount: effectiveAmount, order_desc: orderDesc, payment_type: paymentType, company_name: companyName, plan_id: selectedPlanDetail.id }) }); const data = await readPayload(res); if (!res.ok) throw new Error(data.detail || `Không tạo được liên kết thanh toán ${selectedMethod}`); if (!data.payment_url) throw new Error("Backend không trả về liên kết thanh toán"); if (data.payment_mode === "mock_qr") { setMockPaymentToken(data.token || ""); setMockPaymentUrl(data.payment_url); setMockPaymentState(data.status || "pending"); setMockScanCount(0); setSubmitting(false); return } if (data.payment_mode === "atm_confirm") { try { const nextUrl = new URL(data.payment_url, window.location.origin); navigate(`${nextUrl.pathname}${nextUrl.search}`, { replace: false }); setSubmitting(false); return } catch { window.location.href = data.payment_url; return } } window.location.href = data.payment_url } catch (err) { setError(err.message || "Không thể chuyển tới cổng thanh toán"); setSubmitting(false) } }
  const handleBack = () => { if (isAtmConfirmView) { navigate(`/company-payment?type=${paymentType}&company=${encodeURIComponent(companyName)}`, { replace: true }); return } if (typeof window !== "undefined" && typeof window.history?.state?.idx === "number" && window.history.state.idx > 0) { navigate(-1); return } if (paymentType === "renew") { if (user?.company_access_state === "expired" || user?.company_access_state === "inactive" || user?.company_is_expired) { navigate("/company-status"); return } if (user?.role === "admin" || user?.role === "super_admin") { navigate("/admin/users"); return } navigate("/company-status"); return } navigate("/create-company") }
  if (isAtmConfirmView) return <div style={page}><div style={{ ...shell, maxWidth: 860 }}><section style={{ ...heroCard, padding: isMobile ? 20 : 28 }}><h1 style={heroTitle}>Thanh toán bằng thẻ ATM</h1><p style={heroText}>Nhập thông tin thẻ ATM cho <strong>{companyName}</strong>, sau đó bấm <strong>Thanh toán</strong> để xác nhận giao dịch.</p></section><section style={{ ...sectionCard, padding: isMobile ? 18 : 28 }}><div style={{ ...atmLayout, gridTemplateColumns: isMobile ? "1fr" : atmLayout.gridTemplateColumns }}><div style={atmFormColumn}><h2 style={sectionTitle}>Thông tin thẻ ATM</h2><div style={atmFieldGrid}><label style={atmField}><span style={atmLabel}>Ngân hàng</span><select value={atmForm.bankName} onChange={handleAtmFieldChange("bankName")} style={atmInput}><option value="">Chọn ngân hàng tại Việt Nam</option>{VN_BANK_OPTIONS.map((bank) => <option key={bank} value={bank}>{bank}</option>)}</select><span style={atmFieldMeta} /></label><label style={atmField}><span style={atmLabel}>Chủ thẻ</span><input value={atmForm.cardHolder} onChange={handleAtmFieldChange("cardHolder")} placeholder="NGUYEN VAN A" style={atmInput} /><span style={atmFieldMeta} /></label><label style={atmField}><span style={atmLabel}>Số thẻ</span><input value={atmForm.cardNumber} onChange={handleAtmFieldChange("cardNumber")} placeholder="9704 0000 0000 0000" inputMode="numeric" style={atmInput} /><span style={atmFieldMeta}>{atmCardLengthError ? <span style={atmErrorText}>{atmCardLengthError}</span> : atmForm.bankName ? <span style={atmHelperText}>{atmCardLengthHint}</span> : null}</span></label><label style={atmField}><span style={atmLabel}>Ngày phát hành</span><input value={atmForm.issueDate} onChange={handleIssueDateChange} onBlur={handleIssueDateBlur} placeholder="MM/YY" inputMode="numeric" maxLength={5} style={atmInput} /><span style={atmFieldMeta}>{issueDateError ? <span style={atmErrorText}>{issueDateError}</span> : null}</span></label></div>{error ? <div style={errorBoxLight}>{error}</div> : null}<div style={atmActionRow}><button type="button" onClick={handleConfirmAtmPayment} style={confirmButton} disabled={!isAtmFormComplete || confirmingAtmPayment || showSuccessDialog}>{confirmingAtmPayment ? "Đang xác nhận..." : "Thanh toán"}</button><button type="button" onClick={handleBack} style={lightGhostButton} disabled={confirmingAtmPayment || showSuccessDialog}>Quay lại</button></div></div><aside style={atmSummaryCard}><div style={summaryLabel}>Tóm tắt giao dịch</div><h3 style={{ ...summaryTitle, color: "#0f172a" }}>{companyName}</h3><div style={atmAmount}>{selectedPlanDetail.price}</div><div style={atmSummaryItem}>Gói: {selectedPlanDetail.name}</div><div style={atmSummaryItem}>Thời hạn: {selectedPlanDetail.months}</div><div style={atmSummaryItem}>Phương thức: Thẻ ATM</div><div style={atmHintBox}>Bấm <strong>Thanh toán</strong> để hoàn thành gia hạn.</div></aside></div></section>{successDialog}</div></div>
  return <div style={page}><div style={shell}><section style={{ ...heroCard, padding: isMobile ? 20 : 28 }}><h1 style={heroTitle}>{paymentType === "renew" ? "Gia hạn doanh nghiệp" : "Đăng ký doanh nghiệp"}</h1><p style={heroText}>Vui lòng chọn gói dịch vụ phù hợp cho <strong>{companyName}</strong> để tiếp tục sử dụng hệ thống.</p><div style={{ ...heroMeta, gridTemplateColumns: isMobile ? "1fr" : "repeat(auto-fit, minmax(220px, 1fr))" }}><div style={metaItem}><span style={metaLabel}>Doanh nghiệp</span><strong style={metaValue}>{companyName}</strong></div><div style={metaItem}><span style={metaLabel}>Tài khoản</span><strong style={metaValue}>{user?.email || "Chưa cập nhật"}</strong></div></div></section><section style={{ ...contentGrid, gridTemplateColumns: isMobile ? "1fr" : "minmax(0, 1.85fr) minmax(300px, 0.78fr)" }}><div style={leftCol}><div style={{ ...sectionCard, padding: isMobile ? 18 : 22 }}><h2 style={sectionTitle}>Chọn gói thanh toán</h2><div style={{ ...planGrid, gridTemplateColumns: isMobile ? "1fr" : "repeat(auto-fit, minmax(220px, 1fr))" }}>{PLANS.map((plan) => { const isActive = plan.id === selectedPlan; return <button key={plan.id} type="button" onClick={() => setSelectedPlan(plan.id)} style={{ ...planCard, borderColor: isActive ? plan.accent : "rgba(148, 163, 184, 0.25)", boxShadow: isActive ? `0 18px 40px ${plan.accent}22` : "none", transform: isActive ? "translateY(-2px)" : "none" }}><div style={{ ...planStripe, background: plan.accent }} /><div style={planBody}><div style={planHeader}><span style={planName}>{plan.name}</span>{plan.id !== "1m" ? <span style={offerBadge}>Ưu đãi</span> : null}</div><div style={planPrice}>{plan.price}</div><div style={planMonths}>{plan.months}</div><div style={planBonus}>{plan.bonus}</div></div></button> })}</div></div></div><aside style={{ ...summaryCard, position: isMobile ? "static" : "sticky", top: isMobile ? "auto" : 24, padding: isMobile ? 18 : 20 }}><div style={summaryLabel}>Thông tin thanh toán</div><h3 style={summaryTitle}>{selectedPlanDetail.name}</h3><div style={summaryPrice}>{isZeroTestMode ? "0đ" : selectedPlanDetail.price}</div><div style={summaryItem}>Thời hạn: {selectedPlanDetail.months}</div><div style={summaryItem}>{selectedPlanDetail.bonus}</div>{isZeroTestMode ? <div style={testBadge}>Chế độ test 0đ</div> : null}<div style={summaryDivider} /><div style={paymentMethodBox}><div style={paymentMethodLabel}>Chọn phương thức thanh toán</div><div style={compactMethodList}>{METHODS.map((method) => { const isActive = method.id === selectedMethod; return <button key={method.id} type="button" onClick={() => setSelectedMethod(method.id)} style={{ ...compactMethodCard, borderColor: isActive ? "#7dd3fc" : "rgba(148, 163, 184, 0.22)", background: isActive ? "rgba(125, 211, 252, 0.16)" : "rgba(255, 255, 255, 0.05)" }}><div style={paymentMethodHeader}><img src={method.logoSrc} alt={method.name} style={summaryMethodLogoImage} /><div style={compactMethodMeta}><div style={paymentMethodValue}>{method.name}</div><div style={paymentMethodDesc}>{method.description}</div></div></div></button> })}</div></div>{error ? <div style={errorBox}>{error}</div> : null}{atmStatus === "success" ? <div style={successBox}>Thanh toán thẻ ATM thành công. Hệ thống đang mở trang chính cho doanh nghiệp.</div> : null}{mockPaymentState === "paid" || mockZeroStatus === "paid" ? <div style={successBox}>Đã ghi nhận mock payment thành công. Màn hình sẽ tự cập nhật trạng thái doanh nghiệp.</div> : null}{mockPaymentToken ? <div style={statusCard}><div style={statusTitle}>Trạng thái thanh toán demo</div><div style={statusRow}><span style={statusLabel}>Hiện tại</span><strong style={statusValue}>{mockPaymentState === "paid" ? "Đã thanh toán" : mockPaymentState === "scanned" ? "Đã quét mã" : mockPaymentState === "pending" ? "Đang chờ quét" : "Chưa tạo mã"}</strong></div><div style={statusRow}><span style={statusLabel}>Số lần quét</span><strong style={statusValue}>{mockScanCount}</strong></div></div> : null}{vnpayStatus === "success" ? <div style={successBox}>VNPay đã xác nhận thanh toán thành công. Hệ thống đang mở trang chính cho doanh nghiệp.</div> : null}{vnpayStatus === "failed" ? <div style={errorBox}>VNPay chưa xác nhận thanh toán thành công. Vui lòng thử lại hoặc kiểm tra giao dịch trên cổng VNPay.</div> : null}{mockPaymentUrl ? <div style={qrBox}><div style={qrTitle}>Quét QR để mở trang xác nhận thanh toán demo</div><div style={qrHint}>Khi điện thoại mở link từ mã này, hệ thống sẽ đánh dấu "đã quét mã". Sau đó bạn bấm xác nhận để hoàn tất thanh toán demo.</div><div style={qrCanvas}><QRCode value={mockPaymentUrl} size={isMobile ? 180 : 208} bordered={false} /></div><a href={mockPaymentUrl} target="_blank" rel="noreferrer" style={qrLink}>Mở link test trên thiết bị này</a>{mockPaymentState === "scanned" ? <button type="button" onClick={handleConfirmMockPayment} style={confirmButton} disabled={confirmingMockPayment}>{confirmingMockPayment ? "Đang xác nhận..." : "Xác nhận thanh toán demo"}</button> : null}</div> : null}<button type="button" onClick={handlePay} style={payButton} disabled={submitting || showSuccessDialog}>{displayCtaText}</button><button type="button" onClick={handleBack} style={ghostButton} disabled={submitting || showSuccessDialog}>Quay lại</button></aside></section>{successDialog}</div></div>
}
const page = { minHeight: "100dvh", padding: 24, boxSizing: "border-box", background: "radial-gradient(circle at top left, rgba(14, 165, 233, 0.22) 0%, transparent 28%), linear-gradient(145deg, #08111f 0%, #10233e 55%, #fff7ed 180%)" }
const shell = { maxWidth: 1220, margin: "0 auto", display: "grid", gap: 22, width: "100%" }
const heroCard = { borderRadius: 30, width: "100%", boxSizing: "border-box", background: "rgba(255, 255, 255, 0.92)", boxShadow: "0 30px 90px rgba(15, 23, 42, 0.2)" }
const heroTitle = { margin: "0 0 12px", fontSize: "clamp(30px, 5vw, 44px)", color: "#0f172a" }
const heroText = { margin: "0 0 20px", maxWidth: 760, color: "#334155", lineHeight: 1.7, fontSize: 16 }
const heroMeta = { display: "grid", gap: 14 }
const metaItem = { padding: 18, borderRadius: 18, width: "100%", boxSizing: "border-box", background: "#eff6ff", display: "flex", flexDirection: "column", gap: 8 }
const metaLabel = { fontSize: 12, textTransform: "uppercase", letterSpacing: "0.08em", color: "#64748b" }
const metaValue = { color: "#000", overflowWrap: "anywhere" }
const contentGrid = { display: "grid", gap: 22, alignItems: "start" }
const leftCol = { display: "grid", gap: 22 }
const sectionCard = { borderRadius: 28, width: "100%", boxSizing: "border-box", background: "rgba(255, 255, 255, 0.94)", boxShadow: "0 24px 70px rgba(15, 23, 42, 0.18)" }
const sectionTitle = { margin: "0 0 18px", color: "#0f172a", fontSize: 24 }
const planGrid = { display: "grid", gap: 16, alignItems: "stretch" }
const planCard = { position: "relative", overflow: "hidden", width: "100%", height: "100%", display: "flex", flexDirection: "column", padding: 0, borderRadius: 22, border: "1px solid rgba(148, 163, 184, 0.25)", background: "#fff", cursor: "pointer", textAlign: "left", transition: "all 180ms ease" }
const planStripe = { height: 6, width: "100%" }
const planBody = { padding: 18, display: "grid", gap: 10, flex: 1, alignContent: "start" }
const planHeader = { display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start", minHeight: 32 }
const planName = { fontWeight: 800, color: "#0f172a", lineHeight: 1.35 }
const offerBadge = { padding: "6px 10px", borderRadius: 999, background: "#fef3c7", color: "#92400e", fontSize: 12, fontWeight: 800 }
const planPrice = { fontSize: 30, fontWeight: 900, color: "#0f172a" }
const planMonths = { color: "#1e293b", fontWeight: 600 }
const planBonus = { color: "#475569", lineHeight: 1.6 }
const summaryCard = { borderRadius: 26, width: "100%", boxSizing: "border-box", background: "#0f172a", color: "#f8fafc", boxShadow: "0 24px 80px rgba(2, 6, 23, 0.36)" }
const summaryLabel = { color: "#7dd3fc", textTransform: "uppercase", letterSpacing: "0.12em", fontSize: 12, fontWeight: 800, marginBottom: 10 }
const summaryTitle = { margin: "0 0 10px", fontSize: 24 }
const summaryPrice = { fontSize: 32, fontWeight: 900, marginBottom: 14 }
const summaryItem = { color: "#cbd5e1", lineHeight: 1.7, marginBottom: 8 }
const summaryDivider = { height: 1, background: "rgba(148, 163, 184, 0.22)", margin: "16px 0" }
const testBadge = { display: "inline-flex", marginTop: 4, padding: "6px 10px", borderRadius: 999, background: "rgba(250, 204, 21, 0.14)", color: "#fde68a", border: "1px solid rgba(250, 204, 21, 0.35)", fontSize: 12, fontWeight: 800 }
const paymentMethodBox = { borderRadius: 16, padding: "14px", background: "rgba(255, 255, 255, 0.08)", border: "1px solid rgba(125, 211, 252, 0.24)", marginBottom: 14 }
const paymentMethodLabel = { color: "#7dd3fc", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 800, marginBottom: 10 }
const compactMethodList = { display: "grid", gap: 10 }
const compactMethodCard = { width: "100%", boxSizing: "border-box", padding: "10px 12px", borderRadius: 14, border: "1px solid rgba(148, 163, 184, 0.22)", cursor: "pointer", textAlign: "left", color: "#f8fafc" }
const paymentMethodHeader = { display: "flex", alignItems: "center", gap: 10 }
const summaryMethodLogoImage = { width: 36, height: 36, borderRadius: 10, objectFit: "contain", background: "#fff", padding: 3 }
const compactMethodMeta = { minWidth: 0, display: "grid", gap: 2 }
const paymentMethodValue = { color: "#fff", fontSize: 16, fontWeight: 800 }
const paymentMethodDesc = { color: "#cbd5e1", lineHeight: 1.45, fontSize: 12 }
const errorBox = { marginBottom: 12, borderRadius: 16, padding: "12px 14px", background: "rgba(239, 68, 68, 0.16)", border: "1px solid rgba(248, 113, 113, 0.35)", color: "#fecaca", lineHeight: 1.6 }
const statusCard = { marginBottom: 12, borderRadius: 16, padding: "12px 14px", background: "rgba(125, 211, 252, 0.1)", border: "1px solid rgba(125, 211, 252, 0.24)" }
const statusTitle = { color: "#e0f2fe", fontSize: 13, fontWeight: 800, marginBottom: 10, textTransform: "uppercase", letterSpacing: "0.06em" }
const statusRow = { display: "flex", justifyContent: "space-between", gap: 12, color: "#e2e8f0", marginBottom: 6 }
const statusLabel = { color: "#cbd5e1", fontSize: 13 }
const statusValue = { color: "#f8fafc", fontSize: 13 }
const successBox = { marginBottom: 12, borderRadius: 16, padding: "12px 14px", background: "rgba(34, 197, 94, 0.16)", border: "1px solid rgba(74, 222, 128, 0.35)", color: "#bbf7d0", lineHeight: 1.6 }
const qrBox = { marginBottom: 14, borderRadius: 18, padding: "14px", background: "rgba(255, 255, 255, 0.08)", border: "1px solid rgba(148, 163, 184, 0.24)" }
const qrTitle = { color: "#fff", fontSize: 15, fontWeight: 800, marginBottom: 8 }
const qrHint = { color: "#cbd5e1", fontSize: 13, lineHeight: 1.6, marginBottom: 12 }
const qrCanvas = { display: "flex", justifyContent: "center", padding: "14px 0", background: "#fff", borderRadius: 16, marginBottom: 10 }
const qrLink = { color: "#7dd3fc", fontSize: 13, textDecoration: "underline" }
const atmLayout = { display: "grid", gridTemplateColumns: "minmax(0, 1.5fr) minmax(260px, 0.9fr)", gap: 22, alignItems: "start" }
const atmFormColumn = { display: "grid", gap: 18 }
const atmFieldGrid = { display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }
const atmField = { display: "grid", gap: 8, alignContent: "start" }
const atmLabel = { color: "#334155", fontSize: 13, fontWeight: 700 }
const atmInput = { width: "100%", boxSizing: "border-box", borderRadius: 14, border: "1px solid #cbd5e1", padding: "13px 14px", fontSize: 15, color: "#0f172a", outline: "none", background: "#fff" }
const atmFieldMeta = { minHeight: 18 }
const atmHelperText = { color: "#64748b", fontSize: 12, lineHeight: 1.5 }
const atmErrorText = { color: "#b91c1c", fontSize: 12, lineHeight: 1.5 }
const atmActionRow = { display: "grid", gap: 12 }
const atmSummaryCard = { borderRadius: 22, padding: 20, background: "#eff6ff", border: "1px solid #bfdbfe", display: "grid", gap: 10 }
const atmAmount = { fontSize: 30, fontWeight: 900, color: "#0f172a" }
const atmSummaryItem = { color: "#334155", lineHeight: 1.6 }
const atmHintBox = { marginTop: 6, borderRadius: 16, padding: "12px 14px", background: "#dbeafe", color: "#1e3a8a", lineHeight: 1.6 }
const errorBoxLight = { borderRadius: 16, padding: "12px 14px", background: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", lineHeight: 1.6 }
const confirmButton = { width: "100%", marginTop: 12, border: "none", borderRadius: 14, padding: "12px 14px", background: "linear-gradient(135deg, #22c55e 0%, #14b8a6 100%)", color: "#052e16", fontSize: 14, fontWeight: 900, cursor: "pointer" }
const lightGhostButton = { width: "100%", borderRadius: 14, padding: "12px 14px", background: "transparent", color: "#0f172a", border: "1px solid #cbd5e1", fontSize: 14, fontWeight: 800, cursor: "pointer" }
const payButton = { width: "100%", border: "none", borderRadius: 16, padding: "14px 18px", background: "linear-gradient(135deg, #f59e0b 0%, #fb7185 100%)", color: "#0f172a", fontSize: 16, fontWeight: 900, cursor: "pointer", marginTop: 6 }
const ghostButton = { width: "100%", borderRadius: 16, padding: "13px 18px", background: "transparent", color: "#f8fafc", border: "1px solid rgba(148, 163, 184, 0.35)", fontSize: 15, fontWeight: 700, cursor: "pointer", marginTop: 12 }
const successModalOverlay = { position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.55)", display: "flex", alignItems: "center", justifyContent: "center", padding: 20, zIndex: 1000 }
const successModalCard = { width: "100%", maxWidth: 420, borderRadius: 24, padding: 24, background: "#ffffff", boxShadow: "0 24px 80px rgba(15, 23, 42, 0.25)", textAlign: "center", display: "grid", gap: 14 }
const successModalTitle = { fontSize: 28, fontWeight: 800, color: "#166534" }
const successModalText = { color: "#334155", lineHeight: 1.7, fontSize: 16 }
const successModalButton = { border: "none", borderRadius: 14, padding: "12px 18px", background: "linear-gradient(135deg, #22c55e 0%, #14b8a6 100%)", color: "#052e16", fontSize: 15, fontWeight: 800, cursor: "pointer" }
