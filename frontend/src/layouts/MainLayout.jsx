import { Layout, Menu, Avatar, Dropdown, Button, Input, List } from "antd";
import {
  FolderOpenOutlined,
  PlusOutlined,
  MoreOutlined,
  TeamOutlined,
  UserOutlined,
  LogoutOutlined,
  SearchOutlined,
  BulbOutlined,
  MoonOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";

const { Sider, Content, Header } = Layout;
const APP_THEME_KEY = "app_theme_mode";
const APP_THEMES = {
  dark: {
    background: "#242424",
    surface: "#1d1d1d",
    surfaceMuted: "#2a2a2a",
    text: "#fff",
    mutedText: "#aaa",
    overlay: "rgba(0,0,0,0.55)",
    menuTheme: "dark",
    menuFilter: "none",
    colorScheme: "dark",
  },
  light: {
    background: "linear-gradient(180deg, #f7f3eb 0%, #f4efe6 100%)",
    surface: "rgba(255, 252, 247, 0.96)",
    surfaceMuted: "#e8eefb",
    text: "#1f2937",
    mutedText: "#6b7280",
    overlay: "rgba(15, 23, 42, 0.22)",
    menuTheme: "light",
    menuFilter: "brightness(0.15)",
    colorScheme: "light",
  },
};

export default function MainLayout({
  role,
  user,
  chats = [],
  activeChatId,
  onNewChat,
  onSelectChat,
  onRenameChat,
  onDeleteChat,
  onLogout,
  children,
}) {
  const location = useLocation();
  const navigate = useNavigate();

  const [keyword, setKeyword] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  const [viewportOffsetTop, setViewportOffsetTop] = useState(0);
  const [themeMode, setThemeMode] = useState(() => {
    if (typeof window === "undefined") return "dark";
    const savedTheme = window.localStorage.getItem(APP_THEME_KEY);
    return savedTheme === "light" ? "light" : "dark";
  });
  const isChatPage = location.pathname === "/chat";
  const headerHeight = isMobile ? 56 : 64;
  const siderWidth = 260;
  const activeSiderWidth = isMobile || collapsed ? 0 : siderWidth;
  const theme = APP_THEMES[themeMode] || APP_THEMES.dark;

  useEffect(() => {
    const resize = () => {
      const mobile = window.innerWidth < 768;
      setIsMobile(mobile);
      setCollapsed(mobile);
    };

    resize();
    window.addEventListener("resize", resize);

    return () => window.removeEventListener("resize", resize);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode;
    document.documentElement.style.colorScheme = theme.colorScheme;
    window.localStorage.setItem(APP_THEME_KEY, themeMode);
  }, [themeMode, theme.colorScheme]);

  useEffect(() => {
    if (!isChatPage) return undefined;

    const previousBodyOverflow = document.body.style.overflow;
    const previousBodyOverscroll = document.body.style.overscrollBehavior;
    const previousBodyPosition = document.body.style.position;
    const previousBodyTop = document.body.style.top;
    const previousBodyLeft = document.body.style.left;
    const previousBodyRight = document.body.style.right;
    const previousBodyWidth = document.body.style.width;
    const previousHtmlOverflow = document.documentElement.style.overflow;
    const previousHtmlOverscroll = document.documentElement.style.overscrollBehavior;
    const lockedScrollY = window.scrollY || window.pageYOffset || 0;

    document.body.style.overflow = "hidden";
    document.body.style.overscrollBehavior = "none";
    document.documentElement.style.overflow = "hidden";
    document.documentElement.style.overscrollBehavior = "none";

    if (isMobile) {
      document.body.style.position = "fixed";
      document.body.style.top = `-${lockedScrollY}px`;
      document.body.style.left = "0";
      document.body.style.right = "0";
      document.body.style.width = "100%";
    }

    return () => {
      document.body.style.overflow = previousBodyOverflow;
      document.body.style.overscrollBehavior = previousBodyOverscroll;
      document.body.style.position = previousBodyPosition;
      document.body.style.top = previousBodyTop;
      document.body.style.left = previousBodyLeft;
      document.body.style.right = previousBodyRight;
      document.body.style.width = previousBodyWidth;
      document.documentElement.style.overflow = previousHtmlOverflow;
      document.documentElement.style.overscrollBehavior = previousHtmlOverscroll;
      if (isMobile) {
        window.scrollTo(0, lockedScrollY);
      }
    };
  }, [isChatPage, isMobile]);

  useEffect(() => {
    if (!isChatPage) return undefined;

    const allowScrollWithin = (target) =>
      target instanceof Element && Boolean(target.closest("[data-chat-scroll-allow='true']"));

    const preventMultiTouchViewport = (event) => {
      if (event.touches && event.touches.length > 1) {
        event.preventDefault();
      }
    };

    const preventViewportScroll = (event) => {
      if (allowScrollWithin(event.target)) return;
      event.preventDefault();
    };

    document.addEventListener("touchstart", preventMultiTouchViewport, { passive: false });
    document.addEventListener("touchmove", preventViewportScroll, { passive: false });
    document.addEventListener("wheel", preventViewportScroll, { passive: false });
    document.addEventListener("gesturestart", preventViewportScroll, { passive: false });
    document.addEventListener("gesturechange", preventViewportScroll, { passive: false });
    document.addEventListener("gestureend", preventViewportScroll, { passive: false });

    return () => {
      document.removeEventListener("touchstart", preventMultiTouchViewport);
      document.removeEventListener("touchmove", preventViewportScroll);
      document.removeEventListener("wheel", preventViewportScroll);
      document.removeEventListener("gesturestart", preventViewportScroll);
      document.removeEventListener("gesturechange", preventViewportScroll);
      document.removeEventListener("gestureend", preventViewportScroll);
    };
  }, [isChatPage]);

  useEffect(() => {
    if (!(isMobile && isChatPage)) {
      setViewportOffsetTop(0);
      return undefined;
    }

    const updateViewportOffsetTop = () => {
      const nextOffsetTop = Math.max(0, Math.round(window.visualViewport?.offsetTop || 0));
      setViewportOffsetTop(nextOffsetTop);
    };

    updateViewportOffsetTop();
    window.visualViewport?.addEventListener("resize", updateViewportOffsetTop);
    window.visualViewport?.addEventListener("scroll", updateViewportOffsetTop);

    return () => {
      window.visualViewport?.removeEventListener("resize", updateViewportOffsetTop);
      window.visualViewport?.removeEventListener("scroll", updateViewportOffsetTop);
    };
  }, [isMobile, isChatPage]);

  const filteredChats = chats.filter((c) =>
    (c.title || "").toLowerCase().includes(keyword.toLowerCase())
  );

  const go = (path) => {
    navigate(path);
    if (isMobile) setCollapsed(true);
  };

  const handleLogout = () => {
    onLogout?.();
    navigate("/login");
  };

  const toggleTheme = () => {
    setThemeMode((currentTheme) => (currentTheme === "dark" ? "light" : "dark"));
  };

  const userMenuItems = [
    ...((role === "admin" || role === "super_admin")
      ? [{ key: "/admin/users", icon: <TeamOutlined />, label: "Quản lý doanh nghiệp" }]
      : []),
    { key: "/profile", icon: <UserOutlined />, label: "Quản lý tài khoản" },
    {
      key: "toggle-theme",
      icon: themeMode === "dark" ? <BulbOutlined /> : <MoonOutlined />,
      label: themeMode === "dark" ? "Chế độ sáng" : "Chế độ tối",
    },
    { type: "divider" },
    { key: "logout", icon: <LogoutOutlined />, label: "Đăng xuất", danger: true },
  ];

  return (
    <Layout
      style={{
        minHeight: "100dvh",
        background: theme.background,
      }}
    >

      {/* SIDEBAR */}
      <Sider
        width={260}
        collapsed={collapsed}
        collapsedWidth={0}
        trigger={null}
        style={{
          background: theme.surface,
          position: isMobile ? "fixed" : "relative",
          height: "100dvh",
          zIndex: 1001,
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
          }}
        >

          {/* LOGO */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              padding: 16,
            }}
          >
            <img src="/logo.png" alt="logo" style={{ height: 32 }} />
          </div>

          {/* MENU */}
          <Menu
            theme={theme.menuTheme}
            mode="inline"
            selectedKeys={[]}
            style={{ background: "transparent" }}
            items={[
              {
                key: "/documents",
                icon: <FolderOpenOutlined />,
                label: "Tài liệu",
              },
            ]}
            onClick={({ key }) => go(key)}
          />

          {/* NEW CHAT */}
          <div style={{ padding: 12 }}>
            <Button
              icon={<PlusOutlined />}
              type="primary"
              block
              onClick={() => {
                onNewChat?.();
                if (isMobile) setCollapsed(true);
              }}
            >
              Đoạn chat mới
            </Button>

            <Input
              prefix={<SearchOutlined />}
              placeholder="Tìm đoạn chat"
              style={{ marginTop: 10 }}
              onChange={(e) => setKeyword(e.target.value)}
            />
          </div>

          {/* CHAT LIST */}
          <div
            style={{
              padding: "0 12px",
              overflow: "auto",
              flex: 1,
            }}
          >
            <List
              size="small"
              dataSource={filteredChats}
              renderItem={(item) => (
                <List.Item
                  style={{
                    cursor: "pointer",
                    padding: "8px 10px",
                    borderRadius: 10,
                    background:
                      item._id === activeChatId ? theme.surfaceMuted : "transparent",
                    display: "flex",
                    alignItems: "center",
                  }}
                  onClick={() => {
                    onSelectChat?.(item._id);
                    if (isMobile) setCollapsed(true);
                  }}
                >
                  <div
                    style={{
                      flex: 1,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      color: theme.text,
                    }}
                  >
                    {item.title}
                  </div>

                  <Dropdown
                    trigger={["click"]}
                    menu={{
                      items: [
                        { key: "rename", label: "Đổi tên" },
                        { key: "delete", label: "Xóa", danger: true },
                      ],
                      onClick: ({ key, domEvent }) => {
                        domEvent.stopPropagation();

                        if (key === "rename") {
                          const v = prompt("Tên mới", item.title);
                          if (v) onRenameChat?.(item._id, v);
                        }

                        if (key === "delete") {
                          if (confirm("Xóa chat?"))
                            onDeleteChat?.(item._id);
                        }
                      },
                    }}
                  >
                    <Button
                      type="text"
                      icon={<MoreOutlined />}
                      style={{ color: theme.mutedText }}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Dropdown>
                </List.Item>
              )}
            />
          </div>

          {/* USER */}
          <div
            style={{
              padding: 12,
              marginTop: "auto",
            }}
          >
            <Dropdown
              trigger={["click"]}
              placement="topLeft"
              menu={{
                items: userMenuItems,
                onClick: ({ key }) => {
                  if (key === "logout") handleLogout();
                  else if (key === "toggle-theme") toggleTheme();
                  else go(key);
                },
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  cursor: "pointer",
                }}
              >
                <Avatar src={user?.picture}>
                  {!user?.picture && user?.name?.[0]}
                </Avatar>

                <div style={{ color: theme.text }}>
                  <div style={{ fontWeight: 600 }}>{user?.name}</div>
                  <div style={{ fontSize: 12, color: theme.mutedText }}>{role}</div>
                </div>
              </div>
            </Dropdown>
          </div>

        </div>
      </Sider>

      {/* MOBILE OVERLAY */}
      {isMobile && !collapsed && (
        <div
          onClick={() => setCollapsed(true)}
          style={{
            position: "fixed",
            inset: 0,
            background: theme.overlay,
            zIndex: 1000,
          }}
        />
      )}

      {/* RIGHT SIDE */}
      <Layout
        style={{
          background: theme.background,
          minWidth: 0,
          minHeight: 0,
          overflow: "hidden",
        }}
      >

        {/* HEADER */}
        <Header
          style={{
            background: theme.surface,
            color: theme.text,
            display: "flex",
            alignItems: "center",
            padding: isMobile ? "0 12px" : "0 16px",
            height: headerHeight,
            lineHeight: `${headerHeight}px`,
            flexShrink: 0,
            position: "fixed",
            top: isChatPage && isMobile ? viewportOffsetTop : 0,
            left: activeSiderWidth,
            right: 0,
            zIndex: 999,
            width: `calc(100% - ${activeSiderWidth}px)`,
            boxShadow: themeMode === "dark" ? "0 1px 0 rgba(255,255,255,0.06)" : "0 8px 30px rgba(15, 23, 42, 0.05)",
            transition: "left 0.2s ease, width 0.2s ease",
          }}
        >
          <img
            src="/menu.png"
            alt="Menu"
            onClick={() => setCollapsed(!collapsed)}
            style={{
              cursor: "pointer",
              marginRight: 10,
              width: 24,
              height: 24,
              filter: theme.menuFilter,
              forcedColorAdjust: "none",
            }}
          />

          <span style={{ color: theme.text }}>Trợ lý ảo doanh nghiệp</span>
        </Header>

        {/* CONTENT */}
        <Content
          style={{
            background: theme.background,
            padding: isChatPage ? (isMobile ? 8 : 12) : isMobile ? 12 : 20,
            overflow: isChatPage ? "hidden" : "auto",
            minHeight: 0,
            minWidth: 0,
            marginTop: isChatPage && isMobile ? 0 : headerHeight,
            position: isChatPage && isMobile ? "fixed" : "static",
            top: isChatPage && isMobile ? headerHeight + viewportOffsetTop : undefined,
            left: isChatPage && isMobile ? 0 : undefined,
            right: isChatPage && isMobile ? 0 : undefined,
            bottom: isChatPage && isMobile ? 0 : undefined,
            height: isChatPage && isMobile ? "auto" : `calc(100dvh - ${headerHeight}px)`,
          }}
        >
          {children}
        </Content>

      </Layout>
    </Layout>
  );
}
