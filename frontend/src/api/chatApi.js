import axios from "axios"
import { apiUrl } from "../utils/api"

const API = apiUrl("/api/chats")
const authConfig = (token) => ({
  headers: { Authorization: `Bearer ${token}` }
})

const chatApi = {
  getChats: async (token) => (await axios.get(API, authConfig(token))).data,
  createChat: async (token) => (await axios.post(API, {}, authConfig(token))).data,
  getMessages: async (token, chatId) => (await axios.get(`${API}/${chatId}`, authConfig(token))).data,
  sendMessage: async (token, chatId, content, signal) =>
    (await axios.post(`${API}/${chatId}/messages`, { content }, { ...authConfig(token), signal })).data,
  renameChat: async (token, chatId, title) =>
    (await axios.put(`${API}/${chatId}`, { title }, authConfig(token))).data,
  deleteChat: async (token, chatId) =>
    (await axios.delete(`${API}/${chatId}`, authConfig(token))).data,
  startBackgroundMessage: async (token, chatId, content, signal) =>
    (
      await axios.post(
        `${API}/background`,
        { content, chat_id: chatId },
        { ...authConfig(token), signal },
      )
    ).data,
  stopBackgroundMessage: async (token, messageId, signal) =>
    (await axios.post(`${API}/${messageId}/stop`, {}, { ...authConfig(token), signal })).data,
}

export default chatApi
