import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { apiClient, ApiError } from "../../api/client";
import type { ProviderSnapshot } from "../../api/contracts";
import { providerSettingsSchema, type ProviderSettingsForm } from "./providerSchemas";

const defaults: ProviderSettingsForm = {
  deepseek_api_key: "", seedream_api_key: "", qwen_api_key: "",
  seedream_result_hosts: "ark-content-generation-cn-beijing.tos-cn-beijing.volces.com",
  pricing_version: "official-current",
  deepseek_input_per_million: 1, deepseek_output_per_million: 1,
  seedream_per_image: 1, qwen_input_per_million: 1,
  qwen_output_per_million: 1
};

function formFromSnapshot(value: ProviderSnapshot): ProviderSettingsForm {
  return {
    ...defaults,
    seedream_result_hosts: value.seedream_result_hosts?.join(", ") ?? defaults.seedream_result_hosts,
    pricing_version: value.pricing_version,
    deepseek_input_per_million: value.deepseek_input_per_million ?? defaults.deepseek_input_per_million,
    deepseek_output_per_million: value.deepseek_output_per_million ?? defaults.deepseek_output_per_million,
    seedream_per_image: value.seedream_per_image ?? defaults.seedream_per_image,
    qwen_input_per_million: value.qwen_input_per_million ?? defaults.qwen_input_per_million,
    qwen_output_per_million: value.qwen_output_per_million ?? defaults.qwen_output_per_million,
  };
}

export function ProviderSetup() {
  const cache = useQueryClient();
  const status = useQuery({ queryKey: ["provider-settings"], queryFn: () => apiClient.request<ProviderSnapshot>("/api/v1/settings/providers") });
  const testProvider = useMutation({
    mutationFn: (provider: string) => apiClient.request<{ provider: string; status: string }>(`/api/v1/providers/${provider}/test`, { method: "POST" }),
  });
  const { register, handleSubmit, reset, formState: { errors } } = useForm<ProviderSettingsForm>({ resolver: zodResolver(providerSettingsSchema), defaultValues: defaults });
  useEffect(() => { if (status.data) reset(formFromSnapshot(status.data)); }, [reset, status.data]);
  const save = useMutation({
    mutationFn: (value: ProviderSettingsForm) => apiClient.request<ProviderSnapshot>("/api/v1/settings/providers", {
      method: "PUT", body: {
        ...value,
        deepseek_api_key: value.deepseek_api_key || undefined,
        seedream_api_key: value.seedream_api_key || undefined,
        qwen_api_key: value.qwen_api_key || undefined,
        seedream_result_hosts: value.seedream_result_hosts.split(",").map((item) => item.trim()).filter(Boolean)
      }
    }),
    onSuccess: (value) => { cache.setQueryData(["provider-settings"], value); reset(formFromSnapshot(value)); }
  });

  return <section>
    <header className="page-head"><div><p className="eyebrow">Provider control</p><h1>模型设置</h1><p className="muted">密钥只提交给本机服务，绝不会写入浏览器存储或响应。</p></div><span className={`badge ${status.data?.mode === "cloud" ? "succeeded" : ""}`}>{status.data?.mode === "cloud" ? "云 API 已启用" : "确定性模式"}</span></header>
    <div className="card panel" style={{ maxWidth: 900 }}>
      <div className="grid" style={{ marginBottom: 22 }}>
        {Object.entries(status.data?.providers ?? {}).map(([name, item]) => <div key={name} className="toolbar"><strong style={{ width: 100 }}>{name}</strong><span className={`badge ${item.configured ? "ready" : ""}`}>{item.configured ? `已配置 ····${item.masked_suffix ?? ""}` : "未配置"}</span><span className="muted" style={{ fontSize: 12 }}>{item.backend}</span><button type="button" className="button" disabled={!item.configured || testProvider.isPending} onClick={() => testProvider.mutate(name)}>测试连接</button>{testProvider.data?.provider === name && <span className="success-banner">连接正常</span>}</div>)}
      </div>
      {testProvider.isError && <div className="error-banner" style={{ marginBottom: 16 }}>连接测试失败。密钥不会显示在错误信息中，请检查模型、区域和网络。</div>}
      <form className="form-grid" onSubmit={handleSubmit((value) => save.mutate(value))} autoComplete="off">
        <SecretField label="DeepSeek API Key" name="deepseek_api_key" register={register} />
        <SecretField label="Seedream API Key" name="seedream_api_key" register={register} />
        <SecretField label="Qwen API Key" name="qwen_api_key" register={register} />
        <div className="field"><label>Seedream 图片结果允许域名</label><input {...register("seedream_result_hosts")} /></div>
        <div className="field"><label>价格版本</label><input {...register("pricing_version")} /></div>
        <div className="field"><label>DeepSeek 输入 / 百万 token</label><input type="number" {...register("deepseek_input_per_million", { valueAsNumber: true })} /></div>
        <div className="field"><label>DeepSeek 输出 / 百万 token</label><input type="number" {...register("deepseek_output_per_million", { valueAsNumber: true })} /></div>
        <div className="field"><label>Seedream / 张</label><input type="number" {...register("seedream_per_image", { valueAsNumber: true })} /></div>
        <div className="field"><label>Qwen 输入 / 百万 token</label><input type="number" {...register("qwen_input_per_million", { valueAsNumber: true })} /></div>
        <div className="field"><label>Qwen 输出 / 百万 token</label><input type="number" {...register("qwen_output_per_million", { valueAsNumber: true })} /></div>
        {Object.keys(errors).length > 0 && <div className="error-banner span-2">请检查必填项与价格，所有价格必须为正整数。</div>}
        {save.error && <div className="error-banner span-2">{save.error instanceof ApiError ? save.error.message : "保存失败"}</div>}
        {save.isSuccess && <div className="success-banner span-2">设置已安全保存，{save.data.activation === "pending" ? "当前任务结束后启用" : "已启用"}。</div>}
        <div className="toolbar end span-2"><button className="button primary" disabled={save.isPending}>{save.isPending ? "保存中…" : "保存并启用"}</button></div>
      </form>
    </div>
  </section>;
}

function SecretField({ label, name, register }: { label: string; name: "deepseek_api_key" | "seedream_api_key" | "qwen_api_key"; register: ReturnType<typeof useForm<ProviderSettingsForm>>["register"] }) {
  return <div className="field"><label>{label}</label><input type="password" placeholder="留空则保持现有密钥" autoComplete="new-password" {...register(name)} /></div>;
}
