"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Upload, FileText, File, Trash2, RefreshCw,
  CheckCircle, Clock, XCircle, AlertCircle, Search,
} from "lucide-react";
import { documentsApi, Document } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { formatBytes, formatDate } from "@/lib/utils";

interface UploadProgress {
  name: string;
  progress: number;
  status: "uploading" | "done" | "error";
}

export default function DocumentsPage() {
  const queryClient = useQueryClient();
  const [uploadQueue, setUploadQueue] = useState<UploadProgress[]>([]);
  const [search, setSearch] = useState("");

  const { data: documents = [], isLoading } = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.list({ limit: 100 }).then((r) => r.data),
    refetchInterval: 5000, // Poll for status updates
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => documentsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      toast.success("Document deleted");
    },
  });

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      const newProgress = acceptedFiles.map((f) => ({
        name: f.name,
        progress: 0,
        status: "uploading" as const,
      }));
      setUploadQueue((prev) => [...prev, ...newProgress]);

      for (const file of acceptedFiles) {
        const formData = new FormData();
        formData.append("files", file);

        try {
          await documentsApi.upload(formData);
          setUploadQueue((prev) =>
            prev.map((p) =>
              p.name === file.name ? { ...p, status: "done", progress: 100 } : p
            )
          );
          queryClient.invalidateQueries({ queryKey: ["documents"] });
        } catch {
          setUploadQueue((prev) =>
            prev.map((p) =>
              p.name === file.name ? { ...p, status: "error" } : p
            )
          );
          toast.error(`Failed to upload ${file.name}`);
        }
      }

      // Clear done items after 3s
      setTimeout(() => {
        setUploadQueue((prev) => prev.filter((p) => p.status !== "done"));
      }, 3000);
    },
    [queryClient]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "text/plain": [".txt"],
      "text/markdown": [".md"],
      "text/csv": [".csv"],
      "image/jpeg": [".jpg", ".jpeg"],
      "image/png": [".png"],
    },
    maxSize: 100 * 1024 * 1024,
  });

  const filtered = documents.filter(
    (d) =>
      !search ||
      d.original_filename.toLowerCase().includes(search.toLowerCase()) ||
      d.title?.toLowerCase().includes(search.toLowerCase()) ||
      d.tags?.some((t) => t.toLowerCase().includes(search.toLowerCase()))
  );

  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-2xl font-bold">Documents</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Upload and manage your organization's knowledge base
          </p>
        </div>

        {/* Drop zone */}
        <div
          {...getRootProps()}
          className={cn(
            "rounded-2xl border-2 border-dashed p-10 text-center cursor-pointer transition-all",
            isDragActive
              ? "border-primary bg-primary/5"
              : "border-border hover:border-primary/50 hover:bg-muted/30"
          )}
        >
          <input {...getInputProps()} />
          <Upload
            className={cn(
              "w-10 h-10 mx-auto mb-3 transition-colors",
              isDragActive ? "text-primary" : "text-muted-foreground"
            )}
          />
          <p className="font-medium text-sm">
            {isDragActive ? "Drop files here" : "Drag files here or click to browse"}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            PDF, DOCX, XLSX, TXT, MD, CSV, images · Max 100MB per file
          </p>
        </div>

        {/* Upload queue */}
        {uploadQueue.length > 0 && (
          <div className="space-y-2">
            {uploadQueue.map((item, i) => (
              <UploadQueueItem key={i} item={item} />
            ))}
          </div>
        )}

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search documents..."
            className="w-full pl-9 pr-4 py-2 rounded-lg border border-border bg-muted/30 text-sm outline-none focus:border-primary transition-colors"
          />
        </div>

        {/* Document list */}
        {isLoading ? (
          <div className="space-y-2">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-16 rounded-xl bg-muted/30 animate-pulse" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-16 text-muted-foreground">
            <FileText className="w-10 h-10 mx-auto mb-3 opacity-30" />
            <p className="font-medium">No documents yet</p>
            <p className="text-xs mt-1">Upload files above to get started</p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {filtered.map((doc) => (
              <DocumentRow
                key={doc.id}
                doc={doc}
                onDelete={() => deleteMutation.mutate(doc.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function UploadQueueItem({ item }: { item: UploadProgress }) {
  return (
    <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/30 border border-border text-sm">
      {item.status === "uploading" && (
        <RefreshCw className="w-4 h-4 text-primary animate-spin flex-shrink-0" />
      )}
      {item.status === "done" && (
        <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />
      )}
      {item.status === "error" && (
        <XCircle className="w-4 h-4 text-destructive flex-shrink-0" />
      )}
      <span className="flex-1 truncate">{item.name}</span>
      <span className="text-xs text-muted-foreground capitalize">{item.status}</span>
    </div>
  );
}

const STATUS_CONFIG = {
  pending: { icon: Clock, color: "text-yellow-500", label: "Queued" },
  processing: { icon: RefreshCw, color: "text-blue-500", label: "Processing", spin: true },
  indexed: { icon: CheckCircle, color: "text-green-500", label: "Ready" },
  failed: { icon: AlertCircle, color: "text-destructive", label: "Failed" },
  archived: { icon: File, color: "text-muted-foreground", label: "Archived" },
};

function DocumentRow({ doc, onDelete }: { doc: Document; onDelete: () => void }) {
  const config = STATUS_CONFIG[doc.status] || STATUS_CONFIG.pending;
  const Icon = config.icon;

  return (
    <div className="group flex items-center gap-3 p-3 rounded-xl border border-border/50 hover:border-border hover:bg-muted/20 transition-all">
      <div className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center flex-shrink-0">
        <FileText className="w-4 h-4 text-muted-foreground" />
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">
          {doc.title || doc.original_filename}
        </p>
        <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
          <span>{formatBytes(doc.file_size)}</span>
          {doc.page_count && (
            <>
              <span>·</span>
              <span>{doc.page_count} pages</span>
            </>
          )}
          {doc.language && (
            <>
              <span>·</span>
              <span className="uppercase">{doc.language}</span>
            </>
          )}
          <span>·</span>
          <span>{formatDate(doc.created_at)}</span>
        </div>
      </div>

      {doc.tags && doc.tags.length > 0 && (
        <div className="hidden md:flex gap-1 flex-shrink-0">
          {doc.tags.slice(0, 2).map((tag) => (
            <span key={tag} className="px-1.5 py-0.5 rounded text-[10px] bg-muted text-muted-foreground">
              {tag}
            </span>
          ))}
        </div>
      )}

      <div className={cn("flex items-center gap-1 text-xs flex-shrink-0", config.color)}>
        <Icon className={cn("w-3.5 h-3.5", (config as any).spin && "animate-spin")} />
        <span>{config.label}</span>
      </div>

      <button
        onClick={onDelete}
        className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-all"
        title="Delete document"
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
