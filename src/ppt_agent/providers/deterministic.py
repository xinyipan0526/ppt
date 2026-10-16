"""Network-free Providers used by tests and explicitly selected development mode."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json

from PIL import Image, ImageDraw

from ppt_agent.domain.vision import NormalizedBoxV1, OCRReportV1, OCRSpanV1
from ppt_agent.providers.image import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageUsage,
)
from ppt_agent.providers.text import StructuredTextRequest, StructuredTextResult, TextUsage
from ppt_agent.providers.vision import (
    OCRRequest,
    OCRResult,
    VisionRequest,
    VisionResult,
    VisionUsage,
)


class DeterministicTextProvider:
    """Generate stable schema-valid planning outputs without network access."""

    async def complete(self, request: StructuredTextRequest) -> StructuredTextResult:
        try:
            content = json.loads(request.user_content)
        except json.JSONDecodeError as exc:
            raise ValueError("deterministic provider requires JSON user content") from exc
        title = str(request.json_schema.get("title", ""))
        if request.schema_name == "initial_plan_decision":
            value = self._initial_plan(content)
        elif request.schema_name == "planning_draft":
            value = self._planning_draft(content)
        elif request.schema_name == "brief_decision":
            value = self._brief(content)
        elif request.schema_name == "deck_outline":
            value = self._outline(content)
        elif request.schema_name == "design_system":
            value = self._design()
        elif request.schema_name == "page_specs" and title == "PageSpecPatch":
            value = self._page_patch(content)
        elif request.schema_name == "page_specs":
            value = self._pages(content)
        else:
            raise ValueError(f"unsupported deterministic schema: {request.schema_name}")
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = sha256((request.schema_name + encoded).encode()).hexdigest()
        return StructuredTextResult(
            response_id=f"det-text-{digest[:24]}", value=value,
            usage=TextUsage(
                input_tokens=max(len(request.user_content.encode("utf-8")) // 4, 1),
                output_tokens=max(len(encoded.encode("utf-8")) // 4, 1),
            ),
            response_summary={"response_id": f"det-text-{digest[:24]}", "mode": "deterministic"},
        )

    @classmethod
    def _initial_plan(cls, content: dict) -> dict:
        decision = cls._brief(content)
        if decision["kind"] == "clarification_required":
            return {
                "kind": "clarification_required",
                "plan": None,
                "reason": decision["reason"],
                "questions": decision["questions"],
            }
        brief = decision["brief"]
        outline = cls._outline({"brief": brief})
        design = cls._design()
        pages = cls._pages({"brief": brief, "outline": outline})
        return {
            "kind": "ready",
            "plan": {
                "brief": brief,
                "outline": outline,
                "design_system": design,
                "page_specs": pages,
            },
            "reason": None,
            "questions": [],
        }

    @classmethod
    def _planning_draft(cls, content: dict) -> dict:
        current = content.get("current_plan")
        if not isinstance(current, dict):
            ready = cls._initial_plan(content)
            if ready["kind"] != "ready":
                raise ValueError("full revision requires sufficient planning intent")
            return ready["plan"]
        if isinstance(current.get("page_specs"), dict):
            return current
        brief = current["brief"]
        outline = current["outline"]
        pages = current["pages"]
        return {
            "brief": brief,
            "outline": outline,
            "design_system": current["design_system"],
            "page_specs": {
                "brief_page_count": brief["page_count"],
                "outline": outline,
                "pages": pages,
            },
        }

    @staticmethod
    def _brief(content: dict) -> dict:
        raw = str(content.get("normalized_text", "")).strip()
        compiled_texts: list[str] = []
        source_summaries: list[str] = []
        for line in raw.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                compiled_texts = []
                break
            if isinstance(record, dict) and record.get("kind") == "typed_user_input" and isinstance(record.get("text"), str):
                compiled_texts.append(record["text"])
            elif isinstance(record, dict) and record.get("kind") == "untrusted_source_content":
                if isinstance(record.get("text"), str) and record["text"].strip():
                    source_summaries.append(record["text"][:200])
                elif isinstance(record.get("visual_analysis"), dict) and isinstance(record["visual_analysis"].get("summary"), str):
                    source_summaries.append(record["visual_analysis"]["summary"][:200])
        if compiled_texts or source_summaries:
            raw = " ".join((*compiled_texts, *source_summaries))
        answers = content.get("clarification_answers") or {}
        topic = str(answers.get("topic") or raw).strip()[:200]
        if not topic:
            return {
                "kind": "clarification_required", "brief": None,
                "reason": "缺少演示主题。", "questions": [{
                    "field": "topic", "question": "这份演示要讲什么主题？",
                    "reason": "主题用于生成可执行的演示结构。", "input_type": "text",
                    "options": [], "recommended_value": None, "required": True,
                }],
            }
        if not answers and len(topic) < 5:
            return {
                "kind": "clarification_required", "brief": None,
                "reason": "主题过短，无法可靠判断演示目的和受众。",
                "questions": [
                    {
                        "field": "purpose", "question": "希望这份演示达成什么目的？",
                        "reason": "目的决定叙事结构和行动建议。", "input_type": "text",
                        "options": [], "recommended_value": None, "required": True,
                    },
                    {
                        "field": "audience", "question": "主要受众是谁？",
                        "reason": "受众决定术语、深度和表达方式。", "input_type": "text",
                        "options": [], "recommended_value": None, "required": True,
                    },
                ],
            }
        return {
            "kind": "ready", "brief": {
                "topic": topic, "purpose": str(answers.get("purpose") or "清晰介绍主题"),
                "audience": str(answers.get("audience") or "通用受众"),
                "language": str(answers.get("language") or "zh-CN"),
                "tone": str(answers.get("tone") or "专业简洁"),
                "page_count": int(answers.get("page_count") or 8), "aspect_ratio": "16:9",
                "budget_minor_units": answers.get("budget_minor_units"),
                "quality": str(answers.get("quality") or "balanced"),
            }, "reason": None, "questions": [],
        }

    @staticmethod
    def _outline(content: dict) -> dict:
        brief = content["brief"]
        count = int(brief["page_count"])
        topic = brief["topic"]
        objectives = [
            f"介绍{topic}", f"说明{topic}的背景", f"概览{topic}的核心框架",
            f"展开{topic}的关键要点", f"展示{topic}的实践路径", f"分析{topic}的价值",
            f"给出{topic}的行动建议", f"总结{topic}",
        ]
        pages = [{"page_index": index, "objective": objectives[(index - 1) % len(objectives)], "section": None} for index in range(1, count + 1)]
        return {"narrative": f"从背景、框架、实践到行动完整讲述{topic}。", "pages": pages}

    @staticmethod
    def _design() -> dict:
        return {
            "color_palette": ["#0B132B", "#1C2541", "#3A86FF", "#F8FAFC"],
            "typography": {"heading": "Noto Sans CJK SC", "body": "Noto Sans CJK SC"},
            "visual_style": "clean geometric editorial", "image_style": "minimal modern illustration without text",
            "spacing_scale": [8, 16, 24, 40, 64], "prohibited": ["generated text", "watermark"],
        }

    @classmethod
    def _pages(cls, content: dict) -> dict:
        brief, outline = content["brief"], content["outline"]
        pages = [cls._page(item, brief["topic"], len(outline["pages"])) for item in outline["pages"]]
        return {"brief_page_count": brief["page_count"], "outline": outline, "pages": pages}

    @classmethod
    def _page_patch(cls, content: dict) -> dict:
        outline = content["outline"]
        indexes = set(content.get("revision", {}).get("page_indexes") or content.get("page_indexes") or [])
        if not indexes:
            indexes = {item["page_index"] for item in content.get("current_page_specs", [])}
        topic = content.get("brief", {}).get("topic", "演示主题")
        return {"pages": [cls._page(item, topic, len(outline["pages"])) for item in outline["pages"] if item["page_index"] in indexes]}

    @staticmethod
    def _page(outline_page: dict, topic: str, total: int) -> dict:
        index = int(outline_page["page_index"])
        page_type = "cover" if index == 1 else "closing" if index == total else "content"
        return {
            "page_index": index, "objective": outline_page["objective"], "page_type": page_type,
            "title": topic if index == 1 else outline_page["objective"],
            "body_blocks": [] if index == 1 else [f"要点 {index - 1}", "以清晰、可执行的方式呈现关键信息。"],
            "layout_intent": "clean title and body", "visual_requirements": [],
            "source_refs": [], "acceptance_criteria": ["文字清晰", "版式无溢出"],
        }


class DeterministicImageProvider:
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        fingerprint = request.fingerprint()
        digest = sha256(fingerprint.encode()).digest()
        background = (digest[0], digest[1], digest[2])
        accent = (digest[3], digest[4], digest[5])
        image = Image.new("RGB", (request.width, request.height), background)
        draw = ImageDraw.Draw(image)
        inset = max(min(request.width, request.height) // 8, 1)
        draw.rectangle(
            (inset, inset, request.width - inset, request.height - inset),
            outline=accent,
            width=max(inset // 10, 1),
        )
        output = BytesIO()
        image.save(output, format="PNG", optimize=False, compress_level=9)
        content = output.getvalue()
        response_id = f"det-img-{sha256(content).hexdigest()[:24]}"
        return ImageGenerationResult(
            response_id=response_id,
            media_type="image/png",
            width=request.width,
            height=request.height,
            content=content,
            usage=ImageUsage(
                generated_images=1,
                output_tokens=max((request.width * request.height) // 256, 1),
                input_images=len(request.reference_images),
            ),
            response_summary={"response_id": response_id, "mode": "deterministic"},
        )


class DeterministicVisionProvider:
    async def analyze(self, request: VisionRequest) -> VisionResult:
        fingerprint = request.fingerprint()
        response_id = f"det-vision-{fingerprint.removeprefix('sha256:')[:24]}"
        value = {
            "schema_version": request.schema_version,
            "summary": f"Deterministic analysis {fingerprint[-12:]}",
            "regions": [
                {
                    "label": "primary_visual",
                    "box": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
                    "confidence": 1.0,
                }
            ],
            "contains_generated_text": False,
            "safe_area_intrusion": False,
        }
        return VisionResult(
            response_id=response_id,
            value=value,
            usage=VisionUsage(
                input_tokens=max(len(request.image) // 32, 1),
                output_tokens=max(len(str(value)) // 4, 1),
            ),
            response_summary={"response_id": response_id, "mode": "deterministic"},
        )


class DeterministicOCRProvider:
    async def recognize(self, request: OCRRequest) -> OCRResult:
        text = request.expected_text or ""
        spans = ()
        if text:
            spans = (
                OCRSpanV1(
                    text=text,
                    box=NormalizedBoxV1(x=0.1, y=0.1, width=0.8, height=0.15),
                    confidence=1.0,
                ),
            )
        fingerprint = request.fingerprint()
        response_id = f"det-ocr-{fingerprint.removeprefix('sha256:')[:24]}"
        report = OCRReportV1(full_text=text, spans=spans)
        return OCRResult(
            response_id=response_id,
            report=report,
            usage=VisionUsage(
                input_tokens=max(len(request.image) // 32, 1),
                output_tokens=max(len(text.encode("utf-8")) // 4, 1),
            ),
            response_summary={"response_id": response_id, "mode": "deterministic"},
        )
