import type { PageDetail } from "../../api/contracts";
export function SlideViewer({ page }: { page: PageDetail }) { return <div className="slide-frame">{page.current_generation.page_image_url ? <img src={page.current_generation.page_image_url} alt={`第 ${page.page_index} 页`} /> : <span className="muted">正在生成第 {page.page_index} 页…</span>}</div>; }
