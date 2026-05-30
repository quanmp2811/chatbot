<MainLayout
  role={role}
  user={user}
  chats={chats}
  onNewChat={createNewChat}
  onSelectChat={selectChat}
  onRenameChat={renameChat}
  onDeleteChat={deleteChat}
>
  <ChatWindow currentChat={currentChat} />
</MainLayout>