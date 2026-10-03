import json
from collections.abc import Callable
from pathlib import Path

from differential_coverage.readers.registry import (
    Granularity,
    TrialReader,
    register_reader,
)

# llvm-cov CountedRegion JSON arrays; see renderRegion/renderBranch in
# llvm/tools/llvm-cov/CoverageExporterJson.cpp.
_LINE_START = 0
_COLUMN_START = 1
_LINE_END = 2
_COLUMN_END = 3
_EXEC_COUNT = 4
_REGION_FILE_ID = 5
_REGION_EXPANDED_FILE_ID = 6
_REGION_KIND = 7
_BRANCH_FALSE_COUNT = 5
_BRANCH_FILE_ID = 6

# RegionKind in include/llvm/ProfileData/CoverageMapping.h
_CODE_REGION = 0
_EXPANSION_REGION = 1
_LLVM_EXPORT_MARKER = b"llvm.coverage.json.export"


def _location(record: list[int]) -> str:
    return (
        f"{record[_LINE_START]}:{record[_COLUMN_START]}-"
        f"{record[_LINE_END]}:{record[_COLUMN_END]}"
    )


def _file_keys(filenames: list[str], regions: list[list[int]]) -> Callable[[int], str]:
    """Map file IDs to filenames qualified by their macro expansion call sites.

    Code inside a macro is reported at the macro definition, in a virtual file
    per expansion. Without the call site, all expansions of one macro would
    share edge IDs, conflating coverage of unrelated call sites.
    """
    call_sites = {
        region[_REGION_EXPANDED_FILE_ID]: region
        for region in regions
        if region[_REGION_KIND] == _EXPANSION_REGION
    }
    keys: dict[int, str] = {}

    def key(file_id: int) -> str:
        if file_id not in keys:
            site = call_sites.get(file_id)
            if site is None:
                keys[file_id] = filenames[file_id]
            else:
                parent = key(site[_REGION_FILE_ID])
                keys[file_id] = f"{parent}:{_location(site)}>{filenames[file_id]}"
        return keys[file_id]

    return key


def _edge_id(scope: str, file_key: str, record: list[int]) -> str:
    return f"{scope}@{file_key}:{_location(record)}"


def read(path: Path, *, granularity: Granularity) -> set[str]:
    if granularity == "edge":
        raise ValueError("llvm-cov does not support --granularity edge")
    data = json.loads(path.read_text())
    edges: set[str] = set()
    for export in data.get("data", []):
        for function in export.get("functions", []):
            scope = f"fn:{function['name']}"
            regions = function.get("regions", [])
            file_key = _file_keys(function["filenames"], regions)

            if granularity == "branch":
                for branch in function.get("branches", []):
                    base = _edge_id(scope, file_key(branch[_BRANCH_FILE_ID]), branch)
                    if branch[_EXEC_COUNT] > 0:
                        edges.add(f"{base}:true")
                    if branch[_BRANCH_FALSE_COUNT] > 0:
                        edges.add(f"{base}:false")
            else:
                for region in regions:
                    if region[_EXEC_COUNT] > 0 and region[_REGION_KIND] == _CODE_REGION:
                        edges.add(
                            _edge_id(scope, file_key(region[_REGION_FILE_ID]), region)
                        )
    if not edges:
        raise ValueError(f"No covered edges in {path}")
    return edges


def detect(path: Path) -> bool:
    content = path.read_bytes()
    return _LLVM_EXPORT_MARKER in content and b'"data"' in content


register_reader(TrialReader(name="llvm-cov", read=read, detect=detect))
