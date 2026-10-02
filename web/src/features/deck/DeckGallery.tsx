import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { DeckGeneration, PageDetail } from "../../api/contracts";
import { SlideViewer } from "./SlideViewer";
import { PageInspector } from "./PageInspector";
import { GlobalRegenerateDialog } from "./GlobalRegenerateDialog";
import { ExportPanel } from "../exports/ExportPanel";

type Production = { run_id: string; status: string; pages: Array<{ id: string; page_index: number; status: string }> };

export function DeckGallery({ projectId, deck, onRunCreated }: { projectId: string; deck: DeckGeneration; onRunCreated: (runId: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null); const [regenerate, setRegenerate] = useState(false);
  const production = useQuery({ queryKey: ["production", deck.production_run_id], queryFn: () => apiClient.request<Production>(`/api/v1/runs/${deck.production_run_id}/production`), refetchInterval: deck.status === "succeeded" ? false : 3_000 });
  return <section className="deck-stage"><header className="page-head"><div><p className="eyebrow">Deck generation {deck.generation}</p><h1>完整成品</h1><p className="muted">点击任意页面查看交付文字、设计 Prompt、局部修改和历史版本。</p></div><div className="toolbar"><span className={`badge ${production.data?.status ?? deck.status}`}>{production.data?.status ?? deck.status}</span><button className="button" onClick={() => setRegenerate(true)}>↻ 全局重生成</button></div></header>
    {!production.data && <div className="slide-grid"><div className="skeleton" /><div className="skeleton" /></div>}
    <div className="slide-grid">{production.data?.pages.map((page) => <SlideCard key={page.id} pageId={page.id} onOpen={() => setSelected(page.id)} />)}</div>
    {deck.status === "succeeded" && <div style={{ marginTop: 18 }}><ExportPanel runId={deck.production_run_id} /></div>}
    {selected && <InspectorLoader pageId={selected} onClose={() => setSelected(null)} />}
    {regenerate && <GlobalRegenerateDialog projectId={projectId} deck={deck} onClose={() => setRegenerate(false)} onCreated={onRunCreated} />}
  </section>;
}

function SlideCard({ pageId, onOpen }: { pageId: string; onOpen: () => void }) {
  const page = useQuery({ queryKey: ["page", pageId], queryFn: () => apiClient.request<PageDetail>(`/api/v1/pages/${pageId}`), refetchInterval: 3_000 });
  return <button className="card slide-card" style={{ padding: 0, color: "inherit", textAlign: "left" }} onClick={onOpen}>{page.data ? <><SlideViewer page={page.data} /><div className="slide-info"><strong>{String(page.data.page_index).padStart(2, "0")}</strong><span className={`badge ${page.data.status}`}>{page.data.status}</span></div></> : <div className="skeleton" style={{ aspectRatio: "16/9" }} />}</button>;
}
function InspectorLoader({ pageId, onClose }: { pageId: string; onClose: () => void }) {
  const page = useQuery({ queryKey: ["page", pageId], queryFn: () => apiClient.request<PageDetail>(`/api/v1/pages/${pageId}`) });
  return page.data ? <PageInspector page={page.data} onClose={onClose} /> : null;
}
