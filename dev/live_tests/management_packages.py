"""Small deterministic upload bodies, with no generated files committed to the repo."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile


def skill_zip(key, document, attachment):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "SKILL.md", f"---\nname: {key}\ndescription: Read the exact managed live-test proof.\n---\n\n{document}\n"
        )
        archive.writestr("references/proof.txt", attachment)
    return buffer.getvalue()


async def upload(journey, collection, content, *, content_type="application/octet-stream", params=None):
    from uuid import uuid4

    return await journey.live.request(
        "POST",
        journey.base + "/" + collection,
        expected=201,
        headers={"Idempotency-Key": uuid4().hex, "Content-Type": content_type},
        content=content,
        params=params,
    )


async def publish_skill(journey, key, document, attachment, *, previous=None):
    staged = await upload(
        journey, "skill-uploads", skill_zip(key, document, attachment), content_type="application/zip"
    )
    source = {"kind": "zip_upload", "upload_id": staged["upload_id"]}
    if previous is None:
        return await journey.post(journey.base + "/skills", {"source": source})
    return await journey.post(
        f"/api/v1/skills/{previous['id']}/revisions", {"source": source, "expected_version": previous["version"]}
    )
