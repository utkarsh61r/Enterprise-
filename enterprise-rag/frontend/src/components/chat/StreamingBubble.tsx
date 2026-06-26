"use client";

import { Bot } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Citation } from "@/lib/api/client";

interface StreamingBubbleProps {
  content: string;
  isStreaming: boolean;
  citations: Citation[];
}

export function StreamingBubble({ content, isStreaming }: StreamingBubbleProps) {
  return (
    <div className="flex gap-3 py-3">
      <div className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-1 bg-muted border border-border">
        <Bot className="w-4 h-4 text-foreground" />
      </div>

      <div className="flex flex-col gap-1 max-w-[85%]">
        <div className="rounded-2xl rounded-tl-sm px-4 py-3 text-sm leading-relaxed bg-muted/50 border border-border/50">
          {content ? (
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {content}
              </ReactMarkdown>
            </div>
          ) : null}

          {isStreaming && (
            <span className="inline-flex gap-0.5 ml-0.5">
              <span className="w-1 h-1 rounded-full bg-foreground/60 animate-bounce [animation-delay:0ms]" />
              <span className="w-1 h-1 rounded-full bg-foreground/60 animate-bounce [animation-delay:150ms]" />
              <span className="w-1 h-1 rounded-full bg-foreground/60 animate-bounce [animation-delay:300ms]" />
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
