import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { PageGeneration } from "../../api/contracts";
import { ErrorBanner } from "../../components/ErrorBanner";

export function PageHistory({ pageId, currentId }: { pageId: string; currentId: string }) {
  const cache = useQueryClient();
  const history = useQuery({ queryKey: ["page-history", pageId], queryFn: () => apiClient.request<PageGeneration[]>(`/api/v1/pages/${pageId}/history`) });
  const restore = useMutation({ mutationFn: (source: string) => apiClient.request(`/api/v1/pages/${pageId}/restore`, { method: "POST", body: { source_generation_id: source, expected_current_generation_id: currentId } }), onSuccess: () => { void cache.invalidateQueries({ queryKey: ["page", pageId] }); void cache.invalidateQueries({ queryKey: ["page-history", pageId] }); } });
  return <div className="grid">{history.data?.slice().reverse().map((item) => <div className="toolbar spread" key={item.id}><span>版本 {item.generation}{item.id === currentId ? " · 当前" : ""}</span><button className="button" disabled={item.id === currentId || restore.isPending || !item.page_image_url} onClick={() => restore.mutate(item.id)}>恢复为新版本</button></div>)}{restore.error && <ErrorBanner error={restore.error} fallback="恢复页面版本失败，请稍后重试。" />}</div>;
}
