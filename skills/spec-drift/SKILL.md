---
name: spec-drift
license: Apache-2.0
description: >-
  Use when the user explicitly asks to detect spec drift, or to check that
  code still matches its design documents or READMEs, or when another
  bundled skill calls it. Reports only where code diverges from the docs.
---

# Spec Drift

The goal is to identify all instances where the codebase has diverged from,
or failed to implement, the specifications in a set of design documents or
READMEs. There are two modes:

*   **Directory mode**: the input is a directory of design documents or
    READMEs. Every document in it is checked, and code that no document
    covers is reportable drift (see Guidance).
*   **Targeted mode**: the input names specific design files (and optionally
    a diff range). Check only the code those documents cover, or only the
    changed code if a diff range is given. Code outside the documents' scope
    is not drift. If the caller names a findings file or asks for findings in
    the response, report there instead of the dated report file below.

1.  **Check Input**: Verify that design files or a directory of design
    documents or READMEs are provided. If not, ask the user which to check.
2.  **List Documents**: In directory mode, list all files in the directory;
    in targeted mode, use exactly the files given.
3.  **Analyze**: For EACH design document:

    *   **Contextualize**: Read the design document. Identify the intent,
        proposed changes, and specific filenames/symbols mentioned.
    *   **Locate Source**: Find the corresponding source code files in the
        source tree. If paths aren't explicit, use code search or grep to
        locate the relevant classes, functions, or API endpoints mentioned in
        the doc. Suggest adding the missing links to the document to make
        future detection cheaper (in targeted mode, as a note in the findings).
    *   **Verify**: Read the current state of those source files.
    *   **Detect Drift**: Compare the design vs. reality. Look specifically for:
        *   Missing Features: Logic described in the doc but absent in the code.
        *   Incomplete Models: Dataclasses/Classes/Structs/Protos missing fields
            specified in the design.
        *   Logic Shortcuts: Hardcoded values where dynamic logic was planned.
        *   Architectural Divergence: Implementation using different
            patterns/libraries than proposed.
    *   **Filter**: Report ONLY the differences. Do not describe what is working
        or what matches. If the code matches the design perfectly, output "No
        drift detected."

4.  **Report**: Produce a single report listing all discrepancies found
    (in targeted mode, where the caller says).

    *   Append the analysis for each file to `{YYYY-MM-DD}_DRIFT_REPORT.md`
        using your file-writing tool.
    *   Strict Format:

        ```markdown
        ## <DESIGN_DOC_NAME>

        <Bulleted list of specific divergences found>
        <Or "No drift detected">
        ```

5.  **Guidance**

    -   Be rigorous. If a design doc mentions a specific object, field or file
        name and the code doesn't have it, that is a reportable drift. Double
        check for coverage. In directory mode only, logic that no design or
        README references is also a reportable drift.
    -   Read files liberally to ensure you are looking at the current state
        of the code, not relying on memory.
    -   Process the files sequentially to ensure the tool output buffer doesn't
        overflow.

<!-- Adapted from google/elephant-goldfish-model (Apache-2.0); modified for mission-control. See THIRD_PARTY_NOTICES.md. -->
