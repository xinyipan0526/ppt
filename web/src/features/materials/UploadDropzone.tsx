import { useRef, useState } from "react";

export function UploadDropzone({ onUpload, busy }: { onUpload: (file: File) => void; busy: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const accept = (files: FileList | null) => { const file = files?.item(0); if (file) onUpload(file); };
  return <div className="empty" style={{ minHeight: 112, borderColor: over ? "var(--accent)" : undefined }} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={(e) => { e.preventDefault(); setOver(false); accept(e.dataTransfer.files); }}>
    <div><strong>{busy ? "正在上传…" : "拖入资料"}</strong><p style={{ margin: "6px 0 10px" }}>Markdown、PDF、PNG、JPG、WebP</p><button type="button" className="button" disabled={busy} onClick={() => input.current?.click()}>选择文件</button><input ref={input} hidden type="file" accept=".md,.markdown,.pdf,.png,.jpg,.jpeg,.webp" onChange={(e) => accept(e.target.files)} /></div>
  </div>;
}
