"""Import time benchmarks.

Ensures the package stays fast to import. Slow imports hurt CLI tools,
serverless cold starts, and developer experience.
"""

from __future__ import annotations

import subprocess
import sys


MAX_IMPORT_MS = 200  # fail if import takes longer than this


class TestImportTime:
    def test_lsmsg_import_time(self):
        """lsmsg should import in under 200ms."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import time; s=time.perf_counter(); import lsmsg; print(f'{(time.perf_counter()-s)*1000:.1f}')",
            ],
            capture_output=True,
            text=True,
        )
        ms = float(result.stdout.strip())
        assert ms < MAX_IMPORT_MS, (
            f"lsmsg import took {ms:.1f}ms (max {MAX_IMPORT_MS}ms)"
        )

    def test_lsmsg_testing_import_time(self):
        """lsmsg.testing should import in under 200ms."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import time; s=time.perf_counter(); import lsmsg.testing; print(f'{(time.perf_counter()-s)*1000:.1f}')",
            ],
            capture_output=True,
            text=True,
        )
        ms = float(result.stdout.strip())
        assert ms < MAX_IMPORT_MS, (
            f"lsmsg.testing import took {ms:.1f}ms (max {MAX_IMPORT_MS}ms)"
        )

    def test_no_heavy_imports_at_top_level(self):
        """Verify we don't pull in heavy deps on import."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import lsmsg; mods = [m for m in sys.modules if m.startswith(('boto', 'torch', 'pandas', 'numpy', 'langchain'))]; print(','.join(mods) or 'clean')",
            ],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "clean", (
            f"Heavy modules imported: {result.stdout.strip()}"
        )
