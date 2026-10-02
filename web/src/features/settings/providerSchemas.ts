import { z } from "zod";

export const providerSettingsSchema = z.object({
  deepseek_api_key: z.string().optional(),
  seedream_api_key: z.string().optional(),
  qwen_api_key: z.string().optional(),
  seedream_result_hosts: z.string().min(1, "请填写 Seedream 结果域名"),
  pricing_version: z.string().min(1),
  deepseek_input_per_million: z.number().int().positive(),
  deepseek_output_per_million: z.number().int().positive(),
  seedream_per_image: z.number().int().positive(),
  qwen_input_per_million: z.number().int().positive(),
  qwen_output_per_million: z.number().int().positive()
});
export type ProviderSettingsForm = z.infer<typeof providerSettingsSchema>;
