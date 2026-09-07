"""Build the small, inspectable fixture Wheel without another package workspace."""

import base64
import csv
import hashlib
import io
import zipfile
from pathlib import Path


def approval_wheel() -> tuple[str, bytes]:
    source = Path(__file__).with_name("approval_plugin.py").read_bytes()
    # Changing fixture code produces another immutable version; restart the Worker after an update.
    version = "0.0.0+" + hashlib.sha256(source).hexdigest()[:12]
    distribution = "a13n_live_approval"
    metadata = f"{distribution}-{version}.dist-info"
    files = {
        f"{distribution}/__init__.py": b"",
        f"{distribution}/factory.py": source,
        f"{metadata}/METADATA": (
            f"Metadata-Version: 2.4\nName: a13n-live-approval\nVersion: {version}\nRequires-Python: >=3.13\n\n"
        ).encode(),
        f"{metadata}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
        f"{metadata}/entry_points.txt": b"[a13n_harness.plugins]\nlive.approval = a13n_live_approval.factory:Factory\n",
    }
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + digest, len(content)))
    writer.writerow((f"{metadata}/RECORD", "", ""))
    files[f"{metadata}/RECORD"] = record.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(entry, content)
    return f"{distribution}-{version}-py3-none-any.whl", output.getvalue()
