import json
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException

from blueprint.generator import BlueprintGenerator
from extractor.section_extractor import SectionExtractor
from promps.prompt_builder import PromptBuilder
from drafts.draft_engine import DraftEngine
from assembler.assembler import Assembler


app = FastAPI(title="Legal Drafting API")

# Legacy .doc (OLE) magic bytes
OLE_MAGIC = b"\xd0\xcf\x11\xe0"


def _save_templates(blueprint: dict, templates: dict) -> None:
    """Store blueprint (sections list) and per-section sample content in storage/templates.json."""
    path = Path(__file__).resolve().parent / "storage" / "templates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"blueprint": blueprint, "templates": templates}, f, indent=2)


def _file_to_text(data: bytes, filename: str) -> str:
    """Extract plain text from uploaded file. Supports .txt (UTF-8/cp1252) and .docx."""
    if data.startswith(OLE_MAGIC):
        raise HTTPException(
            status_code=400,
            detail="Legacy .doc (binary) is not supported. Please upload .docx or .txt files.",
        )
    name = (filename or "").lower()
    if name.endswith(".docx"):
        try:
            from docx import Document
            doc = Document(BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid .docx file: {e}") from e
    # Plain text: try UTF-8, then Windows-1252
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1252")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="File could not be decoded as text. Use UTF-8 or Windows-encoded .txt, or .docx.",
            ) from None


@app.post("/generate-draft")
async def generate_draft(
    sample1: UploadFile = File(...),
    sample2: UploadFile = File(...),
    case_summary: str = Form(...),
):
    # Read files (supports .txt and .docx; rejects binary .doc)
    raw1 = await sample1.read()
    raw2 = await sample2.read()
    s1 = _file_to_text(raw1, sample1.filename or "")
    s2 = _file_to_text(raw2, sample2.filename or "")

    try:
        blueprint = BlueprintGenerator().generate(s1, s2)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={"step": "blueprint", "message": str(e)},
        ) from e

    ext = SectionExtractor()
    try:
        t1 = ext.extract(s1, blueprint)
        t2 = ext.extract(s2, blueprint)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={"step": "section_extraction", "message": str(e)},
        ) from e

    # Step 3: Extract templates (section name → sample text from both docs)
    templates = {}
    for sec in blueprint["sections"]:
        name = sec["name"]
        part1 = t1.get(name, "")
        part2 = t2.get(name, "")
        templates[name] = (part1 + "\n" + part2).strip() if (part1 or part2) else ""

    # Persist blueprint + section sample content to templates.json
    _save_templates(blueprint, templates)

    # Step 4: Build dynamic prompts (section name, purpose, sample text)
    builder = PromptBuilder()
    prompts = {}
    for sec in blueprint["sections"]:
        name = sec["name"]
        purpose = sec.get("purpose", "")
        examples = templates.get(name, "")
        prompts[name] = builder.build(name, purpose, examples)

    # Step 5: Generate each section (prompt + case data → AI)
    sections = DraftEngine().generate(prompts, case_summary)

    # Step 6: Assemble draft (join sections in blueprint order)
    final_doc = Assembler().assemble(blueprint, sections)

    return {
        "blueprint": blueprint,
        "final_draft": final_doc
    }


if __name__ == "__main__":
    import uvicorn
    from config import HOST, PORT, DEBUG
    uvicorn.run("main:app", host=HOST, port=PORT, reload=DEBUG)
