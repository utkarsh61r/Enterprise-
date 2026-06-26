/**
 * Enterprise Knowledge Assistant - Chat Store
 *
 * Manages conversations and real-time message streaming state.
 */

import { create } from "zustand";
import {
  chatApi,
  Citation,
  Conversation,
  ConversationDetail,
  Message,
  getAccessToken,
} from "@/lib/api/client";

interface StreamingMessage {
  content: string;
  isStreaming: boolean;
  citations: Citation[];
}

interface ChatState {
  conversations: Conversation[];
  currentConversation: ConversationDetail | null;
  streamingMessage: StreamingMessage | null;
  isLoading: boolean;
  isSending: boolean;
  error: string | null;

  loadConversations: () => Promise<void>;
  loadConversation: (id: string) => Promise<void>;
  createConversation: (title?: string) => Promise<string>;
  deleteConversation: (id: string) => Promise<void>;
  pinConversation: (id: string, pinned: boolean) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  sendMessage: (conversationId: string, content: string) => Promise<void>;
  clearError: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  currentConversation: null,
  streamingMessage: null,
  isLoading: false,
  isSending: false,
  error: null,

  loadConversations: async () => {
    set({ isLoading: true });
    try {
      const response = await chatApi.listConversations({ limit: 100 });
      set({ conversations: response.data, isLoading: false });
    } catch (e: any) {
      set({ error: e?.response?.data?.detail || "Failed to load conversations", isLoading: false });
    }
  },

  loadConversation: async (id) => {
    set({ isLoading: true });
    try {
      const response = await chatApi.getConversation(id);
      set({ currentConversation: response.data, isLoading: false });
    } catch (e: any) {
      set({ error: "Failed to load conversation", isLoading: false });
    }
  },

  createConversation: async (title) => {
    const response = await chatApi.createConversation(title);
    const newConv = response.data;
    set((state) => ({
      conversations: [newConv, ...state.conversations],
    }));
    return newConv.id;
  },

  deleteConversation: async (id) => {
    await chatApi.deleteConversation(id);
    set((state) => ({
      conversations: state.conversations.filter((c) => c.id !== id),
      currentConversation:
        state.currentConversation?.id === id ? null : state.currentConversation,
    }));
  },

  pinConversation: async (id, pinned) => {
    await chatApi.updateConversation(id, { is_pinned: pinned });
    set((state) => ({
      conversations: state.conversations.map((c) =>
        c.id === id ? { ...c, is_pinned: pinned } : c
      ),
    }));
  },

  renameConversation: async (id, title) => {
    await chatApi.updateConversation(id, { title });
    set((state) => ({
      conversations: state.conversations.map((c) =>
        c.id === id ? { ...c, title } : c
      ),
      currentConversation:
        state.currentConversation?.id === id
          ? { ...state.currentConversation!, title }
          : state.currentConversation,
    }));
  },

  sendMessage: async (conversationId, content) => {
    const token = getAccessToken();
    if (!token) return;

    set({ isSending: true, error: null });

    // Add user message optimistically
    const userMessage: Message = {
      id: `temp_${Date.now()}`,
      role: "user",
      content,
      citations: [],
      created_at: new Date().toISOString(),
    };

    set((state) => ({
      currentConversation: state.currentConversation
        ? {
            ...state.currentConversation,
            messages: [...state.currentConversation.messages, userMessage],
          }
        : null,
      streamingMessage: { content: "", isStreaming: true, citations: [] },
    }));

    try {
      for await (const event of chatApi.sendMessageStream(conversationId, content, token)) {
        if (event.type === "token") {
          set((state) => ({
            streamingMessage: state.streamingMessage
              ? {
                  ...state.streamingMessage,
                  content: state.streamingMessage.content + event.content,
                }
              : null,
          }));
        } else if (event.type === "citations") {
          set((state) => ({
            streamingMessage: state.streamingMessage
              ? { ...state.streamingMessage, citations: event.citations }
              : null,
          }));
        } else if (event.type === "done") {
          // Move streaming message to conversation history
          const { streamingMessage } = get();
          const assistantMessage: Message = {
            id: event.message_id,
            role: "assistant",
            content: streamingMessage?.content || "",
            citations: streamingMessage?.citations || [],
            created_at: new Date().toISOString(),
          };

          set((state) => ({
            currentConversation: state.currentConversation
              ? {
                  ...state.currentConversation,
                  messages: [...state.currentConversation.messages, assistantMessage],
                }
              : null,
            streamingMessage: null,
            isSending: false,
          }));

          // Update conversation title if it was the first message
          set((state) => ({
            conversations: state.conversations.map((c) =>
              c.id === conversationId && c.title === "New Conversation"
                ? { ...c, title: content.slice(0, 60) }
                : c
            ),
          }));
        } else if (event.type === "error") {
          set({ error: event.message, streamingMessage: null, isSending: false });
        }
      }
    } catch (e: any) {
      set({
        error: "Failed to send message. Please try again.",
        streamingMessage: null,
        isSending: false,
      });
    }
  },

  clearError: () => set({ error: null }),
}));
