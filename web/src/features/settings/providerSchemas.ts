import { z } from "zod";

const optionalPrice = z.number().int().nonnegative().optional();

export const modelRuntimeSchema = z.object({
  ark_api_key: z.string().optional(),
  multimodal_model_id: z.string().min(1, "请选择多模态语言模型"),
  image_model_id: z.string().min(1, "请选择图片生成模型"),
  multimodal_input_per_million: optionalPrice,
  multimodal_output_per_million: optionalPrice,
  image_output_up_to_2_61mp: optionalPrice,
  image_output_above_2_61mp: optionalPrice,
  image_additional_reference: optionalPrice,
});

export type ModelRuntimeForm = z.infer<typeof modelRuntimeSchema>;
