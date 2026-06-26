"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Bot, User, ChevronDown, ChevronUp, ExternalLink, Copy, ThumbsUp, ThumbsDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Citation, Message } from "@/lib/api/client";
import { toast } from "sonner";
import "highlight.js/styles/github-dark.css";

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const [citationsOpen, setCitationsOpen] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    toast.success("Copied to clipboard");
  };

  return (
    <div className={cn("flex gap-3 py-3 group", isUser && "flex-row-reverse")}>
      {/* Avatar */}
      <div
        className={cn(
          "w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-1",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted border border-border"
        )}
      >
        {isUser ? (
          <User className="w-4 h-4" />
        ) : (
          <Bot className="w-4 h-4 text-foreground" />
        )}
      </div>

      {/* Content */}
      <div className={cn("flex flex-col gap-1 max-w-[85%]", isUser && "items-end")}>
        <div
          className={cn(
            "rounded-2xl px-4 py-3 text-sm leading-relaxed",
            isUser
              ? "bg-primary text-primary-foreground rounded-tr-sm"
              : "bg-muted/50 border border-border/50 rounded-tl-sm"
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none prose-pre:bg-black/30 prose-pre:border prose-pre:border-white/10">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={{
                  code({ node, className, children, ...props }: any) {
                    const isInline = !className;
                    return isInline ? (
                      <code
                        className="bg-muted px-1.5 py-0.5 rounded text-xs font-mono"
                        {...props}
                      >
                        {children}
                      </code>
                    ) : (
                      <code className={cn(className, "text-xs")} {...props}>
                        {children}
                      </code>
                    );
                  },
                  a({ href, children }) {
                    return (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary hover:underline"
                      >
                        {children}
                      </a>
                    );
                  },
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Citations */}
        {!isUser && message.citations && message.citations.length > 0 && (
          <div className="w-full">
            <button
              onClick={() => setCitationsOpen((v) => !v)}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-1"
            >
              {citationsOpen ? (
                <ChevronUp className="w-3 h-3" />
              ) : (
                <ChevronDown className="w-3 h-3" />
              )}
              {message.citations.length} source
              {message.citations.length !== 1 ? "s" : ""}
            </button>

            {citationsOpen && (
              <div className="mt-2 space-y-1.5">
                {message.citations.map((citation, i) => (
                  <CitationCard key={i} citation={citation} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Action bar */}
        {!isUser && (
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity px-1">
            <button
              onClick={handleCopy}
              className="p-1 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
              title="Copy response"
            >
              <Copy className="w-3.5 h-3.5" />
            </button>
            <button
              className="p-1 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
              title="Helpful"
            >
              <ThumbsUp className="w-3.5 h-3.5" />
            </button>
            <button
              className="p-1 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
              title="Not helpful"
            >
              <ThumbsDown className="w-3.5 h-3.5" />
            </button>
            {message.latency_ms && (
              <span className="text-[10px] text-muted-foreground ml-1">
                {(message.latency_ms / 1000).toFixed(1)}s
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function CitationCard({ citation }: { citation: Citation }) {
  return (
    <div className="bg-background border border-border/50 rounded-lg p-2.5 text-xs space-y-1">
      <div className="flex items-start justify-between gap-2">
        <span className="font-medium text-foreground leading-tight line-clamp-1">
          {citation.document_title}
        </span>
        <span
          className={cn(
            "flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium",
            citation.confidence > 0.7
              ? "bg-green-500/10 text-green-500"
              : citation.confidence > 0.4
              ? "bg-yellow-500/10 text-yellow-500"
              : "bg-red-500/10 text-red-500"
          )}
        >
          {Math.round(citation.confidence * 100)}%
        </span>
      </div>

      <div className="flex items-center gap-2 text-muted-foreground">
        {citation.page_number && <span>Page {citation.page_number}</span>}
        {citation.section && (
          <>
            <span>·</span>
            <span className="truncate">{citation.section}</span>
          </>
        )}
      </div>

      {citation.excerpt && (
        <p className="text-muted-foreground line-clamp-2 leading-relaxed">
          "{citation.excerpt}"
        </p>
      )}
    </div>
  );
}
