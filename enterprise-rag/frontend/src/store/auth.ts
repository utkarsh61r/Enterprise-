/**
 * Enterprise Knowledge Assistant - Auth Store
 *
 * Manages authentication state with Zustand.
 * Access token stored in memory only (not localStorage) for security.
 * Refresh token stored in httpOnly cookie (handled by server).
 */

import { create } from "zustand";
import { authApi, setAccessToken, UserProfile } from "@/lib/api/client";

interface AuthState {
  user: UserProfile | null;
  isAuthenticated: boolean;
  isLoading: boolean;

  login: (email: string, password: string) => Promise<void>;
  register: (data: {
    email: string;
    password: string;
    full_name: string;
    organization_name: string;
  }) => Promise<void>;
  logout: () => Promise<void>;
  loadUser: () => Promise<void>;
  updateUser: (user: Partial<UserProfile>) => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,

  login: async (email, password) => {
    const response = await authApi.login(email, password);
    const { access_token, user } = response.data;
    setAccessToken(access_token);
    set({ user, isAuthenticated: true, isLoading: false });
  },

  register: async (data) => {
    const response = await authApi.register(data);
    const { access_token, user } = response.data;
    setAccessToken(access_token);
    set({ user, isAuthenticated: true, isLoading: false });
  },

  logout: async () => {
    try {
      await authApi.logout();
    } catch {
      // Ignore errors on logout
    } finally {
      setAccessToken(null);
      set({ user: null, isAuthenticated: false, isLoading: false });
    }
  },

  loadUser: async () => {
    try {
      // Try to refresh the access token using the httpOnly cookie
      const refreshResponse = await authApi.refresh();
      setAccessToken(refreshResponse.data.access_token);

      const userResponse = await authApi.me();
      set({ user: userResponse.data, isAuthenticated: true, isLoading: false });
    } catch {
      setAccessToken(null);
      set({ user: null, isAuthenticated: false, isLoading: false });
    }
  },

  updateUser: (updates) => {
    const { user } = get();
    if (user) {
      set({ user: { ...user, ...updates } });
    }
  },
}));
