"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useChatStore } from "@/store/chat";
import { useAuthStore } from "@/store/auth";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ChatInput } from "@/components/chat/ChatInput";
import { ConversationSidebar } from "@/components/chat/ConversationSidebar";
import { StreamingBubble } from "@/components/chat/StreamingBubble";
import { Button } from "@/components/ui/button";
import { Bot, Plus, Sparkles } from "lucide-react";
import { toast } from "sonner";

export default function ChatPage() {
  const params = useParams();
  const router = useRouter();
  const conversationId = params?.id as string | undefined;

  const { user } = useAuthStore();
  const {
    conversations,
    currentConversation,
    streamingMessage,
    isSending,
    error,
    loadConversations,
    loadConversation,
    createConversation,
    sendMessage,
    clearError,
  } = useChatStore();

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // Load specific conversation
  useEffect(() => {
    if (conversationId) {
      loadConversation(conversationId);
    }
  }, [conversationId, loadConversation]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [currentConversation?.messages, streamingMessage]);

  // Show errors as toasts
  useEffect(() => {
    if (error) {
      toast.error(error);
      clearError();
    }
  }, [error, clearError]);

  const handleNewChat = async () => {
    const id = await createConversation();
    router.push(`/chat/${id}`);
  };

  const handleSend = async (content: string) => {
    if (!conversationId) {
      // Create new conversation then send
      const id = await createConversation();
      router.push(`/chat/${id}`);
      // Small delay for navigation
      await new Promise((r) => setTimeout(r, 100));
      await sendMessage(id, content);
    } else {
      await sendMessage(conversationId, content);
    }
  };

  const messages = currentConversation?.messages || [];
  const showWelcome = !conversationId || messages.length === 0;

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Sidebar */}
      <ConversationSidebar
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((v) => !v)}
        conversations={conversations}
        currentId={conversationId}
        onNewChat={handleNewChat}
      />

      {/* Main chat area */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="h-14 border-b border-border flex items-center px-4 gap-3">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center">
              <Bot className="w-4 h-4 text-primary" />
            </div>
            <span className="font-semibold text-sm">
              {currentConversation?.title || "Enterprise Knowledge Assistant"}
            </span>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          {showWelcome ? (
            <WelcomeScreen user={user} onSend={handleSend} />
          ) : (
            <div className="max-w-3xl mx-auto px-4 py-6 space-y-1">
              {messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}
              {streamingMessage && (
                <StreamingBubble
                  content={streamingMessage.content}
                  isStreaming={streamingMessage.isStreaming}
                  citations={streamingMessage.citations}
                />
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* Input */}
        <div className="border-t border-border bg-background/95 backdrop-blur">
          <div className="max-w-3xl mx-auto px-4 py-3">
            <ChatInput onSend={handleSend} disabled={isSending} />
          </div>
        </div>
      </div>
    </div>
  );
}

function WelcomeScreen({
  user,
  onSend,
}: {
  user: any;
  onSend: (content: string) => void;
}) {
  const suggestions = [
    "What is our vacation policy?",
    "Summarize the Q3 engineering roadmap",
    "What are the onboarding steps for new hires?",
    "Find documents about the security audit",
  ];

  return (
    <div className="flex flex-col items-center justify-center h-full gap-8 px-4">
      <div className="text-center space-y-3">
        <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mx-auto">
          <Sparkles className="w-8 h-8 text-primary" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight">
          Good {getTimeOfDay()}, {user?.full_name?.split(" ")[0] || "there"}
        </h1>
        <p className="text-muted-foreground text-sm max-w-md">
          Ask me anything about your company's knowledge base. I'll search your
          documents and provide accurate, cited answers.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-xl">
        {suggestions.map((suggestion) => (
          <button
            key={suggestion}
            onClick={() => onSend(suggestion)}
            className="text-left p-3 rounded-xl border border-border hover:border-primary/50 hover:bg-primary/5 transition-all text-sm text-muted-foreground hover:text-foreground"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  );
}

function getTimeOfDay() {
  const hour = new Date().getHours();
  if (hour < 12) return "morning";
  if (hour < 17) return "afternoon";
  return "evening";
}
