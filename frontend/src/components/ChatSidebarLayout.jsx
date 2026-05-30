import { useEffect, useState } from 'react'
import { Tooltip } from 'antd'
import './ChatSidebarLayout.css'

export default function ChatSidebarLayout({
  chats = [],
  activeChatId = null,
  onSelectChat,
  onNewChat,
  children,
}) {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [isMobile, setIsMobile] = useState(typeof window !== 'undefined' ? window.innerWidth < 768 : false)

  useEffect(() => {
    const onResize = () => {
      const mobile = window.innerWidth < 768
      setIsMobile(mobile)
      if (mobile) setSidebarOpen(false)
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const toggleSidebar = () => setSidebarOpen((v) => !v)

  const closeSidebarOnMobile = () => {
    if (isMobile) setSidebarOpen(false)
  }

  return (
    <div className="cgpt-layout">
      <Tooltip title="Toggle sidebar" placement="right">
        <button
          type="button"
          className="cgpt-toggle"
          onClick={toggleSidebar}
          aria-label="Toggle sidebar"
        >
          <span />
          <span />
        </button>
      </Tooltip>

      {isMobile && sidebarOpen && <div className="cgpt-overlay" onClick={closeSidebarOnMobile} />}

      <aside
        className={[
          'cgpt-sidebar',
          sidebarOpen ? 'open' : 'closed',
          isMobile ? 'mobile' : 'desktop',
        ].join(' ')}
      >
        <div className="cgpt-sidebar-header">
          <button type="button" className="cgpt-new-chat" onClick={onNewChat}>
            + New chat
          </button>
        </div>

        <div className="cgpt-chat-list">
          {chats.map((chat) => (
            <button
              key={chat._id}
              type="button"
              className={`cgpt-chat-item ${chat._id === activeChatId ? 'active' : ''}`}
              onClick={() => {
                onSelectChat?.(chat._id)
                closeSidebarOnMobile()
              }}
            >
              {chat.title || 'New chat'}
            </button>
          ))}
        </div>
      </aside>

      <main className={`cgpt-main ${!isMobile && sidebarOpen ? 'with-sidebar' : ''}`}>{children}</main>
    </div>
  )
}

