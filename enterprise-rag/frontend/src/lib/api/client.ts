/**
 * Enterprise Knowledge Assistant - API Client
 *
 * Centralized API client using axios with:
 * - Automatic JWT attachment
 * - 401 → token refresh flow
 * - Request/response logging
 * - Type-safe endpoint methods
 */

import axios, {
  AxiosError,
  AxiosInstance,
  AxiosRequestConfig,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_VERSION = "/api/v1";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export interface ApiError {
  detail: string;
  status: number;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserProfile;
}

export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  role: string;
  organization_id: string;
  organization_name: string;
  avatar_url?: string;
  department?: string;
  mfa_enabled: boolean;
  email_verified: boolean;
  created_at: string;
  last_login_at?: string;
}

export interface Document {
  id: string;
  filename: string;
  original_filename: string;
  status: "pending" | "processing" | "indexed" | "failed" | "archived";
  mime_type: string;
  file_size: number;
  title?: string;
  author?: string;
  department?: string;
  language?: string;
  page_count?: number;
  word_count?: number;
  tags: string[];
  sensitivity: string;
  version: string;
  created_at: string;
  processed_at?: string;
}

export interface Conversation {
  id: string;
  title: string;
  is_pinned: boolean;
  created_at: string;
  message_count?: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  citations: Citation[];
  latency_ms?: number;
  created_at: string;
}

export interface Citation {
  document_id: string;
  document_title: string;
  page_number?: number;
  section?: string;
  excerpt: string;
  confidence: number;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface AnalyticsSummary {
  total_queries: number;
  avg_latency_ms: number;
  total_documents: number;
  total_users: number;
  queries_by_day: { date: string; count: number }[];
  top_documents: { title: string; query_count: number }[];
}

// ─────────────────────────────────────────────────────────────────────────────
// Token Storage
// ─────────────────────────────────────────────────────────────────────────────

let _accessToken: string | null = null;
let _isRefreshing = false;
let _refreshQueue: Array<(token: string | null) => void> = [];

export function setAccessToken(token: string | null) {
  _accessToken = token;
}

export function getAccessToken(): string | null {
  return _accessToken;
}

// ─────────────────────────────────────────────────────────────────────────────
// Axios Instance
// ─────────────────────────────────────────────────────────────────────────────

const api: AxiosInstance = axios.create({
  baseURL: `${API_BASE}${API_VERSION}`,
  withCredentials: true, // Send httpOnly cookie for refresh token
  headers: {
    "Content-Type": "application/json",
  },
});

// Attach access token to every request
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (_accessToken) {
    config.headers.Authorization = `Bearer ${_accessToken}`;
  }
  return config;
});

// Handle 401s: attempt token refresh then replay the failed request
api.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean;
    };

    if (
      error.response?.status === 401 &&
      !originalRequest._retry &&
      !originalRequest.url?.includes("/auth/refresh")
    ) {
      if (_isRefreshing) {
        // Queue this request until refresh completes
        return new Promise((resolve, reject) => {
          _refreshQueue.push((token) => {
            if (token) {
              originalRequest.headers.Authorization = `Bearer ${token}`;
              resolve(api(originalRequest));
            } else {
              reject(error);
            }
          });
        });
      }

      originalRequest._retry = true;
      _isRefreshing = true;

      try {
        const response = await api.post<TokenResponse>("/auth/refresh");
        const newToken = response.data.access_token;
        setAccessToken(newToken);

        // Resolve queued requests
        _refreshQueue.forEach((cb) => cb(newToken));
        _refreshQueue = [];

        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return api(originalRequest);
      } catch (refreshError) {
        // Refresh failed - clear token and redirect to login
        setAccessToken(null);
        _refreshQueue.forEach((cb) => cb(null));
        _refreshQueue = [];

        if (typeof window !== "undefined") {
          window.location.href = "/auth/login";
        }
        return Promise.reject(refreshError);
      } finally {
        _isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// Auth API
// ─────────────────────────────────────────────────────────────────────────────

export const authApi = {
  register: (data: {
    email: string;
    password: string;
    full_name: string;
    organization_name: string;
  }) => api.post<TokenResponse>("/auth/register", data),

  login: (email: string, password: string) =>
    api.post<TokenResponse>("/auth/login", { email, password }),

  logout: () => api.post("/auth/logout"),

  refresh: () => api.post<TokenResponse>("/auth/refresh"),

  me: () => api.get<UserProfile>("/auth/me"),
};

// ─────────────────────────────────────────────────────────────────────────────
// Documents API
// ─────────────────────────────────────────────────────────────────────────────

export const documentsApi = {
  upload: (formData: FormData) =>
    api.post("/documents/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (progressEvent) => {
        // Handled by caller
      },
    }),

  list: (params?: {
    skip?: number;
    limit?: number;
    status_filter?: string;
    department?: string;
    sensitivity?: string;
  }) => api.get<Document[]>("/documents", { params }),

  get: (id: string) => api.get<Document>(`/documents/${id}`),

  getStatus: (id: string) =>
    api.get<{ id: string; status: string; error?: string; processed_at?: string }>(
      `/documents/${id}/status`
    ),

  update: (
    id: string,
    data: {
      title?: string;
      description?: string;
      department?: string;
      tags?: string[];
      sensitivity?: string;
      allowed_roles?: string[];
    }
  ) => api.patch<Document>(`/documents/${id}`, data),

  delete: (id: string) => api.delete(`/documents/${id}`),

  getDownloadUrl: (id: string) =>
    api.get<{ download_token: string; expires_in: number }>(
      `/documents/${id}/download-url`
    ),

  getDownloadLink: (id: string, token: string) =>
    `${API_BASE}${API_VERSION}/documents/${id}/download?token=${token}`,
};

// ─────────────────────────────────────────────────────────────────────────────
// Chat API
// ─────────────────────────────────────────────────────────────────────────────

export const chatApi = {
  createConversation: (title?: string) =>
    api.post<Conversation>("/chat", { title: title || "New Conversation" }),

  listConversations: (params?: { skip?: number; limit?: number }) =>
    api.get<Conversation[]>("/chat", { params }),

  getConversation: (id: string) =>
    api.get<ConversationDetail>(`/chat/${id}`),

  updateConversation: (
    id: string,
    data: { title?: string; is_pinned?: boolean }
  ) => api.patch<Conversation>(`/chat/${id}`, data),

  deleteConversation: (id: string) => api.delete(`/chat/${id}`),

  /**
   * Send a message and stream the response via Server-Sent Events.
   * Returns an EventSource-like async generator.
   */
  sendMessageStream: async function* (
    conversationId: string,
    content: string,
    accessToken: string
  ): AsyncGenerator<
    | { type: "token"; content: string }
    | { type: "citations"; citations: Citation[] }
    | { type: "done"; message_id: string }
    | { type: "error"; message: string }
  > {
    const response = await fetch(
      `${API_BASE}${API_VERSION}/chat/${conversationId}/messages`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        credentials: "include",
        body: JSON.stringify({ content }),
      }
    );

    if (!response.ok) {
      yield { type: "error", message: `HTTP ${response.status}` };
      return;
    }

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            yield data;
          } catch {
            // Skip malformed SSE lines
          }
        }
      }
    }
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Analytics API
// ─────────────────────────────────────────────────────────────────────────────

export const analyticsApi = {
  getSummary: () => api.get<AnalyticsSummary>("/analytics/summary"),
  getQueriesByDay: (days: number = 30) =>
    api.get("/analytics/queries-by-day", { params: { days } }),
};

// ─────────────────────────────────────────────────────────────────────────────
// Admin API
// ─────────────────────────────────────────────────────────────────────────────

export const adminApi = {
  listUsers: (params?: { skip?: number; limit?: number }) =>
    api.get("/admin/users", { params }),

  updateUser: (
    id: string,
    data: { role?: string; is_active?: boolean; department?: string }
  ) => api.patch(`/admin/users/${id}`, data),

  deleteUser: (id: string) => api.delete(`/admin/users/${id}`),

  getWorkerStatus: () => api.get("/admin/workers/status"),

  getAuditLogs: (params?: { skip?: number; limit?: number; action?: string }) =>
    api.get("/admin/audit-logs", { params }),
};

export default api;
