import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import { ErrorBanner } from "../../components/ErrorBanner";

type Field = {
  name: string;
  question?: string;
  reason?: string;
  type: "text" | "number" | "single_select" | "multi_select";
  required?: boolean;
  options?: string[];
  recommended_value?: string | number | string[] | null;
};

export function ClarificationForm({ runId, interrupt, onDone, disabled = false }: { runId: string; interrupt: { id: string; values: Record<string, unknown> }; onDone: () => void; disabled?: boolean }) {
  const schema = (interrupt.values.form_schema ?? {}) as { reason?: string; fields?: Field[] };
  const fields = useMemo(() => schema.fields ?? [], [schema.fields]);
  const initialValues = useMemo(
    () => Object.fromEntries(fields.filter((field) => field.recommended_value != null).map((field) => [field.name, field.recommended_value])),
    [fields],
  );
  const [values, setValues] = useState<Record<string, unknown>>(initialValues);
  const missingRequired = fields.some((field) => field.required !== false && !hasValue(values[field.name]));
  const resume = useMutation({
    mutationFn: () => apiClient.request(`/api/v1/runs/${runId}/interrupts/${interrupt.id}/resume`, {
      method: "POST",
      body: { expected_version: interrupt.values.version, payload: values },
    }),
    onSuccess: onDone,
  });

  return <section className="card panel">
    <p className="eyebrow">需要你的判断</p>
    <h2>补充生成意图</h2>
    {schema.reason && <p className="muted">{schema.reason}</p>}
    <div className="grid">
      {fields.map((field) => <div className="field" key={field.name}>
        <label>{field.question ?? field.name}{field.required !== false ? " *" : ""}</label>
        {field.reason && <span className="muted">{field.reason}</span>}
        <FieldControl field={field} value={values[field.name]} disabled={disabled} onChange={(value) => setValues((current) => ({ ...current, [field.name]: value }))} />
      </div>)}
    </div>
    {resume.error && <ErrorBanner error={resume.error} fallback="提交失败，请重试。" />}
    <div className="toolbar end section-gap">
      <button className="button primary" onClick={() => resume.mutate()} disabled={disabled || resume.isPending || missingRequired}>提交并继续</button>
    </div>
  </section>;
}

function FieldControl({ field, value, disabled, onChange }: { field: Field; value: unknown; disabled: boolean; onChange: (value: unknown) => void }) {
  if (field.type === "single_select") {
    return <select disabled={disabled} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
      <option value="">请选择</option>
      {(field.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}
    </select>;
  }
  if (field.type === "multi_select") {
    const selected = Array.isArray(value) ? value as string[] : [];
    return <div className="choice-list">{(field.options ?? []).map((option) => <label key={option} className="choice">
      <input type="checkbox" disabled={disabled} checked={selected.includes(option)} onChange={(event) => onChange(event.target.checked ? [...selected, option] : selected.filter((item) => item !== option))} />
      <span>{option}</span>
    </label>)}</div>;
  }
  if (field.type === "number") {
    return <input type="number" disabled={disabled} value={value == null ? "" : String(value)} onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.valueAsNumber)} />;
  }
  return <textarea rows={3} disabled={disabled} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} />;
}

function hasValue(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "string") return value.trim().length > 0;
  return value !== undefined && value !== null && !(typeof value === "number" && Number.isNaN(value));
}
