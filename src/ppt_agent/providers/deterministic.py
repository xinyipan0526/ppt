"""Network-free Providers used by tests and explicitly selected development mode."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageDraw

from ppt_agent.domain.vision import NormalizedBoxV1, OCRReportV1, OCRSpanV1
from ppt_agent.providers.image import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageUsage,
)
from ppt_agent.providers.vision import (
    OCRRequest,
    OCRResult,
    VisionRequest,
    VisionResult,
    VisionUsage,
)


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

