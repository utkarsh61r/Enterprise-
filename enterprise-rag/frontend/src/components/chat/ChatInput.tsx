"use client";

import { useState, useRef, KeyboardEvent } from "react";
import { SendHorizonal, Paperclip, Mic } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChatInputProps {
  onSend: (content: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function ChatInput({
  onSend,
  disabled = false,
  placeholder = "Ask anything about your knowledge base...",
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    }
  };

  return (
    <div
      className={cn(
        "flex items-end gap-2 rounded-2xl border border-border bg-muted/30 px-4 py-3 transition-all",
        "focus-within:border-primary/50 focus-within:bg-muted/50",
        disabled && "opacity-60"
      )}
    >
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onInput={handleInput}
        disabled={disabled}
        placeholder={placeholder}
        rows={1}
        className={cn(
          "flex-1 resize-none bg-transparent text-sm leading-relaxed",
          "placeholder:text-muted-foreground outline-none",
          "min-h-[24px] max-h-[200px]"
        )}
        aria-label="Message input"
      />

      <div className="flex items-center gap-1 pb-0.5">
        <button
          disabled={!value.trim() || disabled}
          onClick={handleSend}
          className={cn(
            "w-8 h-8 rounded-xl flex items-center justify-center transition-all",
            value.trim() && !disabled
              ? "bg-primary text-primary-foreground hover:bg-primary/90"
              : "bg-muted text-muted-foreground cursor-not-allowed"
          )}
          aria-label="Send message"
        >
          {disabled ? (
            <div className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
          ) : (
            <SendHorizonal className="w-3.5 h-3.5" />
          )}
        </button>
      </div>
    </div>
  );
}
