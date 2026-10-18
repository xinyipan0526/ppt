import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { apiClient } from "../../api/client";
import type { ModelCatalogItem, ModelRuntimeSnapshot } from "../../api/contracts";
import { modelRuntimeSchema, type ModelRuntimeForm } from "./providerSchemas";
import { ErrorBanner } from "../../components/ErrorBanner";

const emptyForm: ModelRuntimeForm = {
  ark_api_key: "",
  multimodal_model_id: "",
  image_model_id: "",
};

const overrideNames = [
  "multimodal_input_per_million",
  "multimodal_output_per_million",
  "image_output_up_to_2_61mp",
  "image_output_above_2_61mp",
  "image_additional_reference",
] as const;

function recommended(catalog: ModelCatalogItem[], category: "multimodal" | "image") {
  return catalog.find((item) => item.category === category && item.lifecycle === "stable" && item.recommended)
    ?? catalog.find((item) => item.category === category && item.lifecycle === "stable");
}

function formFromSnapshot(snapshot: ModelRuntimeSnapshot, catalog: ModelCatalogItem[]): ModelRuntimeForm {
  const overrides = snapshot.pricing_overrides ?? {};
  return {
    ark_api_key: "",
    multimodal_model_id: snapshot.multimodal_model?.model_id ?? recommended(catalog, "multimodal")?.model_id ?? "",
    image_model_id: snapshot.image_model?.model_id ?? recommended(catalog, "image")?.model_id ?? "",
    ...Object.fromEntries(overrideNames.map((name) => [name, overrides[name]])),
  };
}

function requestBody(value: ModelRuntimeForm) {
  const pricing_overrides = Object.fromEntries(
    overrideNames
      .filter((name) => value[name] !== undefined)
      .map((name) => [name, value[name]]),
  );
  return {
    ...(value.ark_api_key ? { ark_api_key: value.ark_api_key } : {}),
    multimodal_model_id: value.multimodal_model_id,
    image_model_id: value.image_model_id,
    ...(Object.keys(pricing_overrides).length ? { pricing_overrides } : {}),
  };
}

export function ProviderSetup() {
  const cache = useQueryClient();
  const catalog = useQuery({
    queryKey: ["model-catalog"],
    queryFn: () => apiClient.request<ModelCatalogItem[]>("/api/v1/model-catalog"),
  });
  const status = useQuery({
    queryKey: ["model-runtime"],
    queryFn: () => apiClient.request<ModelRuntimeSnapshot>("/api/v1/settings/model-runtime"),
  });
  const { register, handleSubmit, reset, formState: { errors } } = useForm<ModelRuntimeForm>({
    resolver: zodResolver(modelRuntimeSchema),
    defaultValues: emptyForm,
  });

  useEffect(() => {
    if (status.data && catalog.data) reset(formFromSnapshot(status.data, catalog.data));
  }, [catalog.data, reset, status.data]);

  const save = useMutation({
    mutationFn: async (value: ModelRuntimeForm) => {
      const body = requestBody(value);
      await apiClient.request("/api/v1/adapters/volcengine_ark/test", { method: "POST", body });
      return apiClient.request<ModelRuntimeSnapshot>("/api/v1/settings/model-runtime", { method: "PUT", body });
    },
    onSuccess: (value) => {
      cache.setQueryData(["model-runtime"], value);
      reset(formFromSnapshot(value, catalog.data ?? []));
    },
  });
  const remove = useMutation({
    mutationFn: () => apiClient.request<ModelRuntimeSnapshot>(
      "/api/v1/adapters/volcengine_ark/credential", { method: "DELETE" },
    ),
    onSuccess: (value) => {
      cache.setQueryData(["model-runtime"], value);
      reset(formFromSnapshot(value, catalog.data ?? []));
    },
  });

  const configured = status.data?.adapter_status.configured ?? false;
  const models = catalog.data ?? [];
  const multimodal = models.filter((item) => item.category === "multimodal" && item.lifecycle === "stable");
  const images = models.filter((item) => item.category === "image" && item.lifecycle === "stable");
  const mutationError = save.error ?? remove.error;

  return <section>
    <header className="page-head">
      <div>
        <p className="eyebrow">Model runtime</p>
        <h1>模型设置</h1>
        <p className="muted">只需一个火山方舟 API Key，并选择一个多模态模型和一个图片模型。价格、端点和结果域名由模型目录管理。</p>
      </div>
      <span className={`badge ${status.data?.mode === "cloud" ? "succeeded" : ""}`}>
        {status.data?.mode === "cloud" ? "云 API 已启用" : "确定性模式"}
      </span>
    </header>
    {!configured && <div className="card panel settings-panel settings-notice" role="status">
      <strong>完成首次配置</strong>
      <p className="muted">填写 Ark API Key 并选择两个模型。保存前会先验证凭据，密钥不会显示在页面或响应中。</p>
    </div>}
    <div className="card panel settings-panel">
      <form className="form-grid" onSubmit={handleSubmit((value) => save.mutate(value))} autoComplete="off">
        <div className="field span-2">
          <label htmlFor="ark-api-key">火山方舟 API Key</label>
          <input
            id="ark-api-key"
            type="password"
            placeholder={configured ? "留空则继续使用已保存的密钥" : "请输入火山方舟 API Key"}
            autoComplete="new-password"
            required={!configured}
            {...register("ark_api_key")}
          />
        </div>
        <div className="field">
          <label htmlFor="multimodal-model">多模态语言模型</label>
          <select id="multimodal-model" required {...register("multimodal_model_id")}>
            <option value="">请选择模型</option>
            {multimodal.map((item) => <option key={item.model_id} value={item.model_id}>
              {item.display_name}{item.recommended ? "（推荐）" : ""}
            </option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="image-model">图片生成模型</label>
          <select id="image-model" required {...register("image_model_id")}>
            <option value="">请选择模型</option>
            {images.map((item) => <option key={item.model_id} value={item.model_id}>
              {item.display_name}{item.recommended ? "（推荐）" : ""}
            </option>)}
          </select>
        </div>
        <details className="span-2">
          <summary>高级设置（可选）</summary>
          <p className="muted">通常无需修改。留空时使用版本化模型目录中的官方默认价格。</p>
          <div className="form-grid">
            <PriceField label="多模态输入 / 百万 token" name="multimodal_input_per_million" register={register} />
            <PriceField label="多模态输出 / 百万 token" name="multimodal_output_per_million" register={register} />
            <PriceField label="图片 ≤2.61MP" name="image_output_up_to_2_61mp" register={register} />
            <PriceField label="图片 >2.61MP" name="image_output_above_2_61mp" register={register} />
            <PriceField label="额外参考图" name="image_additional_reference" register={register} />
          </div>
        </details>
        {Object.keys(errors).length > 0 && <div className="error-banner span-2">请填写 API Key 并选择两个受支持的模型；高级价格只能是非负整数。</div>}
        {mutationError && <ErrorBanner error={mutationError} fallback="测试、保存或删除凭据失败，请稍后重试。" />}
        {save.isSuccess && <div className="success-banner span-2">设置已安全保存，{save.data.activation === "pending" ? "当前任务结束后启用" : "已启用"}。</div>}
        <div className="toolbar end span-2">
          {configured && <button type="button" className="button danger" disabled={remove.isPending} onClick={() => remove.mutate()}>删除凭据</button>}
          <button className="button primary" disabled={save.isPending || catalog.isLoading || status.isLoading}>
            {save.isPending ? "测试中…" : "测试并启用"}
          </button>
        </div>
      </form>
    </div>
  </section>;
}

function PriceField({ label, name, register }: {
  label: string;
  name: typeof overrideNames[number];
  register: ReturnType<typeof useForm<ModelRuntimeForm>>["register"];
}) {
  return <div className="field">
    <label>{label}<input type="number" min="0" {...register(name, {
      setValueAs: (value) => value === "" ? undefined : Number(value),
    })} /></label>
  </div>;
}
