import { useEffect, useRef, useState } from 'react'
import { AudioOutlined, ArrowDownOutlined, ArrowUpOutlined } from '@ant-design/icons'
import { Button, Input, Layout, Tooltip } from 'antd'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import chatApi from '../api/chatApi'
import { apiUrl } from '../utils/api'
import './Chat.css'

const { Content } = Layout
const { TextArea } = Input

const STREAM_STATE_KEY = 'chat_pending_stream_state'
const POLL_INTERVAL_MS = 2000
const PAUSED_NOTICE = 'Đã tạm dừng tạo câu trả lời.'
const STOPPED_NOTICE = 'Đã dừng tạo câu trả lời.'
const SEARCHING_NOTICE = 'Đang tìm kiếm...'
const NEW_CHAT_NOTICE = 'Bắt đầu cuộc trò chuyện mới!'
const INPUT_PLACEHOLDER = 'Nhập câu hỏi...'
const OPEN_DOC_LABEL = 'Mở tài liệu'

const markdownComponents = {
  p: ({ children }) => <p className="chat-message-paragraph">{children}</p>,
  h1: ({ children }) => <h1 className="chat-message-h1">{children}</h1>,
  h2: ({ children }) => <h2 className="chat-message-h2">{children}</h2>,
  h3: ({ children }) => <h3 className="chat-message-h3">{children}</h3>,
  ul: ({ children }) => <ul className="chat-message-list">{children}</ul>,
  ol: ({ children }) => <ol className="chat-message-list">{children}</ol>,
  li: ({ children }) => <li className="chat-message-list-item">{children}</li>,
  code({ inline, className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || '')
    const language = match?.[1]

    return !inline ? (
      <pre className="chat-message-pre" data-language={language}>
        <code className={className} {...props}>
          {children}
        </code>
      </pre>
    ) : (
      <code className="chat-message-code" {...props}>
        {children}
      </code>
    )
  },
  table: ({ children }) => (
    <div className="chat-message-table-wrap">
      <table className="chat-message-table">{children}</table>
    </div>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
}

function renderMessageSource(message) {
  if (message.role !== 'assistant' || !message.source) return null
  if (!message.source.url && !message.source.download_api && !message.source.file_id) return null

  const labelName = message.source.file_name || message.source.file_id
  if (!labelName) return null

  const label = `${OPEN_DOC_LABEL}: ${labelName}`
  const href = message.source.url || (message.source.file_id ? `https://drive.google.com/open?id=${message.source.file_id}` : null)

  return (
    <div className="chat-message-source">
      {href ? (
        <a href={href} target="_blank" rel="noreferrer" className="chat-message-source-link">
          {label}
        </a>
      ) : (
        <span className="chat-message-source-text">{label}</span>
      )}
    </div>
  )
}

function readPendingStreamState() {
  try {
    const raw = sessionStorage.getItem(STREAM_STATE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function writePendingStreamState(chatId, notice) {
  if (!chatId) return
  try {
    sessionStorage.setItem(
      STREAM_STATE_KEY,
      JSON.stringify({
        chatId,
        notice,
        savedAt: Date.now(),
      }),
    )
  } catch {}
}

function clearPendingStreamState() {
  try {
    sessionStorage.removeItem(STREAM_STATE_KEY)
  } catch {}
}

function isAbortError(error) {
  return (
    error?.name === 'CanceledError' ||
    error?.code === 'ERR_CANCELED' ||
    error?.name === 'AbortError'
  )
}

function getMessageOrderValue(message) {
  if (message?.role === 'user') return 0
  if (message?.role === 'assistant') return 1
  return 2
}

function getMessageTimeValue(message, fallbackIndex) {
  const rawValue = message?.created_at || message?.createdAt || message?.savedAt || null
  const parsed = rawValue ? new Date(rawValue).getTime() : Number.NaN
  if (Number.isFinite(parsed)) return parsed
  return fallbackIndex
}

function normalizeMessages(messages) {
  return [...(messages || [])]
    .map((message, index) => ({ message, index }))
    .sort((leftEntry, rightEntry) => {
      const left = leftEntry.message
      const right = rightEntry.message
      const leftTime = getMessageTimeValue(left, leftEntry.index)
      const rightTime = getMessageTimeValue(right, rightEntry.index)

      if (leftTime !== rightTime) return leftTime - rightTime

      const orderDiff = getMessageOrderValue(left) - getMessageOrderValue(right)
      if (orderDiff !== 0) return orderDiff

      return String(left?._id || '').localeCompare(String(right?._id || ''))
    })
    .map((entry) => entry.message)
}

function enforceMessagePairs(messages, pairMap) {
  if (!Array.isArray(messages) || !messages.length || !(pairMap instanceof Map) || pairMap.size === 0) {
    return messages || []
  }

  const next = [...messages]

  pairMap.forEach((userId, assistantId) => {
    const userIndex = next.findIndex((message) => message?._id === userId)
    const assistantIndex = next.findIndex((message) => message?._id === assistantId)

    if (userIndex < 0 || assistantIndex < 0 || userIndex < assistantIndex) return

    const [assistantMessage] = next.splice(assistantIndex, 1)
    const targetUserIndex = next.findIndex((message) => message?._id === userId)
    if (targetUserIndex >= 0) {
      next.splice(targetUserIndex + 1, 0, assistantMessage)
    } else {
      next.push(assistantMessage)
    }
  })

  return next
}

export default function Chat({
  accessToken,
  currentChatId,
  onCreateChat,
  onSelectChat,
  onChatTitleUpdated,
}) {
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([])
  const [sending, setSending] = useState(false)
  const [keyboardInset, setKeyboardInset] = useState(0)
  const [viewportMetrics, setViewportMetrics] = useState({
    offsetLeft: 0,
    offsetTop: 0,
    width: typeof window !== 'undefined' ? window.innerWidth : 0,
  })
  const [composerFrame, setComposerFrame] = useState({
    left: 0,
    width: 700,
  })
  const [isMobileViewport, setIsMobileViewport] = useState(
    typeof window !== 'undefined' ? window.innerWidth < 768 : false,
  )
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const bottomRef = useRef(null)
  const chatScrollRef = useRef(null)
  const composerRef = useRef(null)
  const contentRef = useRef(null)
  const abortRef = useRef(null)
  const activeChatRef = useRef(currentChatId)
  const pollingAssistantRef = useRef(null)
  const messagePairRef = useRef(new Map())
  const shouldStickToBottomRef = useRef(true)
  const forceAutoScrollRef = useRef(false)
  const isLightTheme = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light'
  const themeColors = isLightTheme
    ? {
        text: '#1f2937',
        mutedText: '#6b7280',
        userBubble: '#5b5b5b',
        userText: '#ffffff',
        assistantBubble: 'transparent',
        assistantBorder: 'transparent',
        composerBackground: 'rgba(255, 252, 247, 0.96)',
        composerBorder: '#ddd6c9',
        composerShadow: '0 18px 40px rgba(15, 23, 42, 0.1)',
        scrollButtonBackground: 'rgba(255, 252, 247, 0.98)',
        scrollButtonBorder: '#ddd6c9',
        scrollButtonIcon: '#1f2937',
        iconMuted: '#64748b',
        sendButtonBackground: '#1d4ed8',
        sendButtonColor: '#fff',
        sendButtonShadow: '0 10px 24px rgba(29, 78, 216, 0.22)',
        stopButtonColor: '#1f2937',
      }
    : {
        text: '#fff',
        mutedText: '#9ca3af',
        userBubble: '#5a5a5a',
        userText: '#ffffff',
        assistantBubble: 'transparent',
        assistantBorder: 'transparent',
        composerBackground: 'rgba(24, 24, 27, 0.9)',
        composerBorder: '#3a3a3a',
        composerShadow: '0 -6px 24px rgba(0,0,0,0.28)',
        scrollButtonBackground: 'rgba(24, 24, 27, 0.92)',
        scrollButtonBorder: 'rgba(255,255,255,0.16)',
        scrollButtonIcon: '#fff',
        iconMuted: '#bbb',
        sendButtonBackground: '#fff',
        sendButtonColor: '#000',
        sendButtonShadow: '0 2px 6px rgba(0,0,0,0.2)',
        stopButtonColor: '#000',
      }

  useEffect(() => {
    activeChatRef.current = currentChatId
  }, [currentChatId])

  const applyMessages = (nextMessages) => {
    setMessages(enforceMessagePairs(normalizeMessages(nextMessages), messagePairRef.current))
  }

  const clearActivePolling = () => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    pollingAssistantRef.current = null
  }

  const scrollToLatest = (behavior = 'smooth') => {
    const scrollContainer = chatScrollRef.current
    if (scrollContainer) {
      scrollContainer.scrollTo({
        top: scrollContainer.scrollHeight,
        behavior,
      })
      return
    }

    bottomRef.current?.scrollIntoView({ behavior })
  }

  const updateStickToBottom = () => {
    const scrollContainer = chatScrollRef.current
    if (!scrollContainer) {
      shouldStickToBottomRef.current = true
      setShowScrollToBottom(false)
      return
    }

    const distanceFromBottom =
      scrollContainer.scrollHeight - scrollContainer.scrollTop - scrollContainer.clientHeight
    shouldStickToBottomRef.current = distanceFromBottom < 96
    setShowScrollToBottom(distanceFromBottom >= 96)
  }

  const openMessageSource = async (source) => {
    if (!source) return

    if (source.download_api) {
      try {
        const response = await fetch(apiUrl(`/api${source.download_api}`), {
          headers: { Authorization: `Bearer ${accessToken}` },
        })
        if (!response.ok) {
          throw new Error(`Không mở được tài liệu (${response.status})`)
        }

        const blob = await response.blob()
        const objectUrl = window.URL.createObjectURL(blob)
        window.open(objectUrl, '_blank', 'noopener,noreferrer')
        window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 60_000)
        return
      } catch (error) {
        console.error('Open local document error:', error)
      }
    }

    const href = source.url || (source.file_id ? `https://drive.google.com/open?id=${source.file_id}` : null)
    if (href) {
      window.open(href, '_blank', 'noopener,noreferrer')
    }
  }

  const pollBackgroundAnswer = async (chatId, assistantId, controller) => {
    pollingAssistantRef.current = assistantId

    try {
      let completed = false

      while (!completed) {
        if (controller.signal.aborted) {
          throw new DOMException('Aborted', 'AbortError')
        }

        await new Promise((resolve, reject) => {
          const timeoutId = window.setTimeout(resolve, POLL_INTERVAL_MS)
          const abortListener = () => {
            window.clearTimeout(timeoutId)
            reject(new DOMException('Aborted', 'AbortError'))
          }

          controller.signal.addEventListener('abort', abortListener, { once: true })
        })

        const refreshed = await chatApi.getMessages(accessToken, chatId)
        if (!Array.isArray(refreshed) || !refreshed.length) {
          continue
        }

        applyMessages(refreshed)
        const assistantMessage = refreshed.find((message) => message._id === assistantId)
        if (assistantMessage && assistantMessage.status !== 'processing') {
          completed = true
          clearPendingStreamState()
        }
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
      }
      if (pollingAssistantRef.current === assistantId) {
        pollingAssistantRef.current = null
      }
      setSending(false)
    }
  }

  useEffect(() => {
    clearActivePolling()
    setSending(false)

    if (!currentChatId || !accessToken) {
      messagePairRef.current = new Map()
      setMessages([])
      return undefined
    }

    let cancelled = false

    const loadMessages = async () => {
      try {
        const data = await chatApi.getMessages(accessToken, currentChatId)
        if (cancelled) return

        let nextMessages = data || []
        const pendingState = readPendingStreamState()
        if (pendingState?.chatId === currentChatId && pendingState?.notice) {
          nextMessages = [
            ...nextMessages,
            {
              _id: `pending-${pendingState.savedAt || Date.now()}`,
              role: 'assistant',
              content: pendingState.notice,
            },
          ]
        }

        applyMessages(nextMessages)

        const processingMessage = [...(data || [])]
          .reverse()
          .find((message) => message.role === 'assistant' && message.status === 'processing')

        if (processingMessage && !abortRef.current) {
          const controller = new AbortController()
          abortRef.current = controller
          setSending(true)
          writePendingStreamState(currentChatId, PAUSED_NOTICE)

          pollBackgroundAnswer(currentChatId, processingMessage._id, controller).catch((error) => {
            if (!isAbortError(error)) {
              console.error('Resume background polling error:', error)
            }
          })
          return
        }

        clearPendingStreamState()
      } catch (error) {
        console.error('Load messages error:', error)
      }
    }

    loadMessages()

    return () => {
      cancelled = true
      clearActivePolling()
    }
  }, [accessToken, currentChatId])

  useEffect(() => {
    if (forceAutoScrollRef.current || shouldStickToBottomRef.current) {
      scrollToLatest(forceAutoScrollRef.current ? 'smooth' : 'auto')
      forceAutoScrollRef.current = false
    }
  }, [messages])

  useEffect(() => {
    if (typeof window === 'undefined') return undefined

    const updateViewportOffset = () => {
      const isTouchLayout = window.innerWidth < 768
      setIsMobileViewport(isTouchLayout)
      if (!isTouchLayout) {
        setKeyboardInset(0)
        const contentRect = contentRef.current?.getBoundingClientRect()
        const availableWidth = Math.max(320, Math.min(700, Math.round((contentRect?.width || 0) - 24)))
        const centeredLeft = contentRect
          ? Math.round(contentRect.left + Math.max(0, ((contentRect.width || 0) - availableWidth) / 2))
          : Math.max(12, Math.round((window.innerWidth - availableWidth) / 2))
        setComposerFrame({
          left: centeredLeft,
          width: availableWidth,
        })
        return
      }

      const viewport = window.visualViewport
      if (!viewport) {
        setKeyboardInset(0)
        setViewportMetrics({
          offsetLeft: 0,
          offsetTop: 0,
          width: window.innerWidth,
        })
        return
      }

      const rawInset = Math.max(
        0,
        Math.round(window.innerHeight - viewport.height - viewport.offsetTop),
      )
      const nextInset = rawInset > 120 ? rawInset : 0
      setKeyboardInset(nextInset)
      setViewportMetrics({
        offsetLeft: Math.max(0, Math.round(viewport.offsetLeft || 0)),
        offsetTop: Math.max(0, Math.round(viewport.offsetTop || 0)),
        width: Math.max(0, Math.round(viewport.width || window.innerWidth)),
      })

      const activeElement = document.activeElement
      const isTyping =
        activeElement instanceof HTMLElement &&
        composerRef.current?.contains(activeElement)

      if (isTyping || nextInset > 0) {
        window.requestAnimationFrame(() => scrollToLatest('auto'))
      }
    }

    updateViewportOffset()
    window.addEventListener('resize', updateViewportOffset)
    window.addEventListener('scroll', updateViewportOffset, { passive: true })
    window.visualViewport?.addEventListener('resize', updateViewportOffset)
    window.visualViewport?.addEventListener('scroll', updateViewportOffset)

    return () => {
      window.removeEventListener('resize', updateViewportOffset)
      window.removeEventListener('scroll', updateViewportOffset)
      window.visualViewport?.removeEventListener('resize', updateViewportOffset)
      window.visualViewport?.removeEventListener('scroll', updateViewportOffset)
    }
  }, [])

  useEffect(() => {
    window.requestAnimationFrame(() => {
      updateStickToBottom()
    })
  }, [currentChatId])

  useEffect(() => {
    const handlePageExit = () => {
      if (!abortRef.current || !activeChatRef.current) return
      writePendingStreamState(activeChatRef.current, PAUSED_NOTICE)
      abortRef.current.abort()
    }

    window.addEventListener('beforeunload', handlePageExit)
    window.addEventListener('pagehide', handlePageExit)

    return () => {
      window.removeEventListener('beforeunload', handlePageExit)
      window.removeEventListener('pagehide', handlePageExit)
    }
  }, [])

  useEffect(() => {
    if (!isMobileViewport || !composerRef.current) return undefined

    const composerElement = composerRef.current
    const textareaElement = composerElement.querySelector('textarea')
    const preventComposerDrag = (event) => {
      event.preventDefault()
    }

    composerElement.addEventListener('touchmove', preventComposerDrag, { passive: false })
    textareaElement?.addEventListener('touchmove', preventComposerDrag, { passive: false })

    return () => {
      composerElement.removeEventListener('touchmove', preventComposerDrag)
      textareaElement?.removeEventListener('touchmove', preventComposerDrag)
    }
  }, [isMobileViewport])

  const ensureChat = async () => {
    if (currentChatId) return currentChatId

    const created = await onCreateChat?.()
    const createdId = created?._id || null
    if (createdId) {
      onSelectChat?.(createdId)
    }
    return createdId
  }

  const sendMessage = async () => {
    if (!input.trim() || sending || !accessToken) return

    setSending(true)

    try {
      const chatId = await ensureChat()
      if (!chatId) return

      const text = input.trim()
      setInput('')
      forceAutoScrollRef.current = true

      const now = Date.now()
      const userTempId = `tmp-user-${now}`
      const assistantTempId = `tmp-ai-${now}`
      messagePairRef.current.set(assistantTempId, userTempId)

      setMessages((prev) => [
        ...prev,
        { _id: userTempId, role: 'user', content: text },
        { _id: assistantTempId, role: 'assistant', content: SEARCHING_NOTICE },
      ])

      const controller = new AbortController()
      abortRef.current = controller
      const started = await chatApi.startBackgroundMessage(
        accessToken,
        chatId,
        text,
        controller.signal,
      )

      if (started?.chat_title && onChatTitleUpdated) {
        onChatTitleUpdated(chatId, started.chat_title)
      }

      const startedMessages = [started?.user_message, started?.assistant_message].filter(Boolean)
      if (startedMessages.length) {
        const startedUserId = started?.user_message?._id
        const startedAssistantId = started?.assistant_message?._id
        if (startedUserId && startedAssistantId) {
          messagePairRef.current.delete(assistantTempId)
          messagePairRef.current.set(startedAssistantId, startedUserId)
        }

        setMessages((prev) => {
          const withoutTemps = prev.filter(
            (message) => message._id !== userTempId && message._id !== assistantTempId,
          )
          return enforceMessagePairs(
            normalizeMessages([...withoutTemps, ...startedMessages]),
            messagePairRef.current,
          )
        })
      }

      const assistantId = started?.assistant_message?._id
      if (!assistantId) {
        throw new Error('Không tạo được tiến trình trả lời')
      }

      await pollBackgroundAnswer(chatId, assistantId, controller)

      if (onChatTitleUpdated) {
        const chats = await chatApi.getChats(accessToken)
        const updated = Array.isArray(chats) ? chats.find((chat) => chat._id === chatId) : null
        if (updated?.title) {
          onChatTitleUpdated(chatId, updated.title)
        }
      }
    } catch (error) {
      if (isAbortError(error)) {
        setMessages((prev) => {
          const next = [...prev]
          for (let i = next.length - 1; i >= 0; i -= 1) {
            if (next[i].role === 'assistant') {
              next[i] = { ...next[i], content: PAUSED_NOTICE, status: 'paused' }
              break
            }
          }
          return next
        })
      } else {
        console.error('Send message error:', error)
        clearPendingStreamState()
        setSending(false)
      }
    } finally {
      if (!abortRef.current) {
        setSending(false)
      }
    }
  }

  const stopGenerating = async () => {
    if (!abortRef.current) return

    const assistantId = pollingAssistantRef.current
    const controller = abortRef.current
    controller.abort()

    if (assistantId && accessToken) {
      try {
        await chatApi.stopBackgroundMessage(accessToken, assistantId)
      } catch (error) {
        if (!isAbortError(error)) {
          console.error('Stop message error:', error)
        }
      }
    }

    setMessages((prev) =>
      prev.map((message) =>
        message._id === assistantId
          ? { ...message, content: STOPPED_NOTICE, status: 'stopped' }
          : message,
      ),
    )
    clearPendingStreamState()
    setSending(false)
  }

  return (
    <Layout style={{ height: '100%', minHeight: 0, background: 'transparent' }}>
      <Content
        ref={contentRef}
        style={{
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          minHeight: 0,
          padding: 16,
          width: '100%',
          maxWidth: 900,
          margin: '0 auto',
        }}
      >
        <div
          ref={chatScrollRef}
          data-chat-scroll-allow="true"
          className="chat-scroll"
          onScroll={updateStickToBottom}
          style={{
            color: themeColors.text,
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            padding: `12px 8px ${Math.max(isMobileViewport ? 132 : 96, 108 + keyboardInset + (isMobileViewport ? 24 : 0))}px`,
            overscrollBehavior: 'contain',
            WebkitOverflowScrolling: 'touch',
          }}
        >
          {messages.length === 0 && (
            <div style={{ color: themeColors.mutedText, textAlign: 'center', marginTop: 24 }}>
              {NEW_CHAT_NOTICE}
            </div>
          )}

          {messages.map((message, index) => (
            <div
              key={message._id || index}
              style={{
                marginBottom: 20,
                marginTop: 20,
                display: 'flex',
                justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start',
              }}
            >
              <div
                style={{
                  maxWidth: message.role === 'assistant' ? '100%' : '80%',
                  padding: '10px 12px',
                  borderRadius: 12,
                  lineHeight: 1.5,
                  background: message.role === 'user' ? themeColors.userBubble : themeColors.assistantBubble,
                  color: message.role === 'user' ? themeColors.userText : themeColors.text,
                  border: message.role === 'assistant' ? `1px solid ${themeColors.assistantBorder}` : 'none',
                }}
              >
                <div className={`chat-message-content${message.role === 'user' ? ' chat-message-content--user' : ''}`}>
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    rehypePlugins={[rehypeHighlight]}
                    components={markdownComponents}
                  >
                    {String(message.content || '')}
                  </ReactMarkdown>
                </div>
                {message.role === 'assistant' && message.source ? (
                  <div className="chat-message-source">
                    <button
                      type="button"
                      className="chat-message-source-link"
                      onClick={() => openMessageSource(message.source)}
                    >
                      {`${OPEN_DOC_LABEL}: ${message.source.file_name || message.source.file_id}`}
                    </button>
                  </div>
                ) : null}
              </div>
            </div>
          ))}

          <div ref={bottomRef} />
        </div>

        <div
          ref={composerRef}
          style={{
            position: 'fixed',
            left: isMobileViewport ? viewportMetrics.offsetLeft + 8 : composerFrame.left,
            right: undefined,
            bottom: 8,
            display: 'flex',
            alignItems: 'flex-end',
            gap: 8,
            width: isMobileViewport ? Math.max(280, viewportMetrics.width - 16) : composerFrame.width,
            maxWidth: isMobileViewport ? Math.max(280, viewportMetrics.width - 16) : composerFrame.width,
            padding: 12,
            marginBottom: 0,
            borderRadius: 24,
            background: themeColors.composerBackground,
            border: `1px solid ${themeColors.composerBorder}`,
            zIndex: 20,
            boxShadow: themeColors.composerShadow,
            touchAction: 'none',
            overscrollBehavior: 'none',
          }}
        >
          {showScrollToBottom ? (
            <Button
              type="text"
              shape="circle"
              aria-label="Cuộn xuống tin nhắn mới nhất"
              onClick={() => {
                forceAutoScrollRef.current = true
                scrollToLatest('smooth')
                shouldStickToBottomRef.current = true
                setShowScrollToBottom(false)
              }}
              icon={<ArrowDownOutlined style={{ fontSize: 18, color: themeColors.scrollButtonIcon }} />}
              style={{
                position: 'absolute',
                left: '50%',
                top: -54,
                transform: 'translateX(-50%)',
                width: 42,
                height: 42,
                borderRadius: '999px',
                border: `1px solid ${themeColors.scrollButtonBorder}`,
                background: themeColors.scrollButtonBackground,
                boxShadow: '0 10px 24px rgba(0,0,0,0.28)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            />
          ) : null}

          <TextArea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            autoSize={{ minRows: 1, maxRows: 6 }}
            className="chat-input"
            placeholder={INPUT_PLACEHOLDER}
            bordered={false}
            onFocus={() => {
              window.setTimeout(() => scrollToLatest('smooth'), 120)
            }}
            onPressEnter={(event) => {
              if (!event.shiftKey) {
                event.preventDefault()
                sendMessage()
              }
            }}
            style={{
              flex: 1,
              background: 'transparent',
              color: themeColors.text,
              resize: 'none',
              touchAction: 'none',
              overscrollBehavior: 'none',
            }}
          />

          <Tooltip title="Ghi âm">
            <Button
              shape="circle"
              type="text"
              icon={<AudioOutlined style={{ fontSize: 20 }} />}
              style={{ border: 'none', background: 'transparent', color: themeColors.iconMuted }}
              onClick={() => console.log('Voice clicked')}
            />
          </Tooltip>

          <Tooltip title={sending ? 'Dừng tạo câu trả lời' : 'Gửi tin nhắn'}>
            <Button
              shape="circle"
              size="large"
              onClick={sending ? stopGenerating : sendMessage}
              disabled={!sending && !input.trim()}
              icon={
                sending ? (
                  <div
                    style={{
                      width: 14,
                      height: 14,
                      background: themeColors.stopButtonColor,
                      borderRadius: 2,
                    }}
                  />
                ) : (
                  <ArrowUpOutlined style={{ fontSize: 18 }} />
                )
              }
              style={{
                width: 40,
                height: 40,
                minWidth: 40,
                background: themeColors.sendButtonBackground,
                color: themeColors.sendButtonColor,
                border: 'none',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: themeColors.sendButtonShadow,
              }}
            />
          </Tooltip>
        </div>
      </Content>
    </Layout>
  )
}
