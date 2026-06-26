"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Plus, MessageSquare, Pin, PinOff, Pencil, Trash2,
  ChevronLeft, ChevronRight, Bot, Upload, LayoutDashboard,
  Settings, LogOut,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Conversation } from "@/lib/api/client";
import { useChatStore } from "@/store/chat";
import { useAuthStore } from "@/store/auth";
import { toast } from "sonner";

interface ConversationSidebarProps {
  open: boolean;
  onToggle: () => void;
  conversations: Conversation[];
  currentId?: string;
  onNewChat: () => void;
}

export function ConversationSidebar({
  open,
  onToggle,
  conversations,
  currentId,
  onNewChat,
}: ConversationSidebarProps) {
  const router = useRouter();
  const { deleteConversation, pinConversation, renameConversation } = useChatStore();
  const { user, logout } = useAuthStore();

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const pinned = conversations.filter((c) => c.is_pinned);
  const unpinned = conversations.filter((c) => !c.is_pinned);

  const handleDelete = async (id: string) => {
    await deleteConversation(id);
    if (id === currentId) router.push("/chat");
    toast.success("Conversation deleted");
  };

  const handleRename = async (id: string) => {
    if (renameValue.trim()) {
      await renameConversation(id, renameValue.trim());
    }
    setRenamingId(null);
    setRenameValue("");
  };

  const handleLogout = async () => {
    await logout();
    router.push("/auth/login");
  };

  return (
    <aside
      className={cn(
        "flex flex-col border-r border-border bg-card transition-all duration-300 flex-shrink-0",
        open ? "w-64" : "w-12"
      )}
    >
      {/* Toggle & Brand */}
      <div className="h-14 flex items-center px-3 border-b border-border gap-2">
        {open && (
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <div className="w-7 h-7 rounded-lg bg-primary flex items-center justify-center flex-shrink-0">
              <Bot className="w-4 h-4 text-primary-foreground" />
            </div>
            <span className="font-semibold text-sm truncate">EKA</span>
          </div>
        )}
        <button
          onClick={onToggle}
          className="w-7 h-7 rounded-lg hover:bg-muted flex items-center justify-center flex-shrink-0 text-muted-foreground hover:text-foreground transition-colors"
        >
          {open ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>
      </div>

      {/* New Chat */}
      <div className="p-2">
        <button
          onClick={onNewChat}
          className={cn(
            "w-full flex items-center gap-2 px-3 py-2 rounded-lg",
            "bg-primary/10 hover:bg-primary/20 text-primary transition-colors text-sm font-medium",
            !open && "justify-center px-0"
          )}
        >
          <Plus className="w-4 h-4 flex-shrink-0" />
          {open && <span>New Chat</span>}
        </button>
      </div>

      {/* Nav Links */}
      {open && (
        <div className="px-2 pb-2 space-y-0.5">
          {[
            { href: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
            { href: "/documents", icon: Upload, label: "Documents" },
          ].map(({ href, icon: Icon, label }) => (
            <Link
              key={href}
              href={href}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              <Icon className="w-4 h-4" />
              {label}
            </Link>
          ))}
        </div>
      )}

      {/* Conversations */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-3">
        {open && pinned.length > 0 && (
          <div>
            <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-3 py-1">
              Pinned
            </p>
            <ConversationList
              conversations={pinned}
              currentId={currentId}
              renamingId={renamingId}
              renameValue={renameValue}
              onRenameStart={(c) => { setRenamingId(c.id); setRenameValue(c.title); }}
              onRenameChange={setRenameValue}
              onRenameSubmit={handleRename}
              onRenameCancel={() => setRenamingId(null)}
              onPin={pinConversation}
              onDelete={handleDelete}
            />
          </div>
        )}

        {open && unpinned.length > 0 && (
          <div>
            {pinned.length > 0 && (
              <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-3 py-1">
                Recent
              </p>
            )}
            <ConversationList
              conversations={unpinned}
              currentId={currentId}
              renamingId={renamingId}
              renameValue={renameValue}
              onRenameStart={(c) => { setRenamingId(c.id); setRenameValue(c.title); }}
              onRenameChange={setRenameValue}
              onRenameSubmit={handleRename}
              onRenameCancel={() => setRenamingId(null)}
              onPin={pinConversation}
              onDelete={handleDelete}
            />
          </div>
        )}

        {!open && (
          <div className="space-y-1">
            {conversations.slice(0, 8).map((c) => (
              <Link
                key={c.id}
                href={`/chat/${c.id}`}
                className={cn(
                  "flex items-center justify-center w-8 h-8 mx-auto rounded-lg transition-colors",
                  currentId === c.id
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
                title={c.title}
              >
                <MessageSquare className="w-4 h-4" />
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* User footer */}
      <div className="border-t border-border p-2">
        {open ? (
          <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-muted transition-colors group">
            <div className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0 text-xs font-semibold text-primary">
              {user?.full_name?.[0]?.toUpperCase() || "U"}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium truncate">{user?.full_name}</p>
              <p className="text-[10px] text-muted-foreground truncate capitalize">{user?.role}</p>
            </div>
            <button
              onClick={handleLogout}
              className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-muted transition-all text-muted-foreground hover:text-foreground"
              title="Sign out"
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <button
            onClick={handleLogout}
            className="w-8 h-8 mx-auto flex items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            title="Sign out"
          >
            <LogOut className="w-4 h-4" />
          </button>
        )}
      </div>
    </aside>
  );
}

function ConversationList({
  conversations,
  currentId,
  renamingId,
  renameValue,
  onRenameStart,
  onRenameChange,
  onRenameSubmit,
  onRenameCancel,
  onPin,
  onDelete,
}: any) {
  return (
    <div className="space-y-0.5">
      {conversations.map((conv: Conversation) => (
        <ConversationItem
          key={conv.id}
          conversation={conv}
          isActive={conv.id === currentId}
          isRenaming={renamingId === conv.id}
          renameValue={renameValue}
          onRenameStart={() => onRenameStart(conv)}
          onRenameChange={onRenameChange}
          onRenameSubmit={() => onRenameSubmit(conv.id)}
          onRenameCancel={onRenameCancel}
          onPin={(pinned: boolean) => onPin(conv.id, pinned)}
          onDelete={() => onDelete(conv.id)}
        />
      ))}
    </div>
  );
}

function ConversationItem({
  conversation,
  isActive,
  isRenaming,
  renameValue,
  onRenameStart,
  onRenameChange,
  onRenameSubmit,
  onRenameCancel,
  onPin,
  onDelete,
}: any) {
  return (
    <div
      className={cn(
        "group relative flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors cursor-pointer",
        isActive ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"
      )}
    >
      <MessageSquare className="w-3.5 h-3.5 flex-shrink-0" />

      {isRenaming ? (
        <input
          autoFocus
          value={renameValue}
          onChange={(e) => onRenameChange(e.target.value)}
          onBlur={onRenameSubmit}
          onKeyDown={(e) => {
            if (e.key === "Enter") onRenameSubmit();
            if (e.key === "Escape") onRenameCancel();
          }}
          className="flex-1 bg-transparent outline-none text-foreground text-xs"
        />
      ) : (
        <Link
          href={`/chat/${conversation.id}`}
          className="flex-1 truncate text-xs"
        >
          {conversation.title}
        </Link>
      )}

      {!isRenaming && (
        <div className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 flex-shrink-0">
          <button
            onClick={onRenameStart}
            className="p-0.5 rounded hover:bg-white/10"
            title="Rename"
          >
            <Pencil className="w-3 h-3" />
          </button>
          <button
            onClick={() => onPin(!conversation.is_pinned)}
            className="p-0.5 rounded hover:bg-white/10"
            title={conversation.is_pinned ? "Unpin" : "Pin"}
          >
            {conversation.is_pinned ? (
              <PinOff className="w-3 h-3" />
            ) : (
              <Pin className="w-3 h-3" />
            )}
          </button>
          <button
            onClick={onDelete}
            className="p-0.5 rounded hover:bg-white/10 text-destructive"
            title="Delete"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}
