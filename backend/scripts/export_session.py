"""Convert a Claude Code session transcript (JSONL) into Markdown.

Claude Code keeps transcripts at ~/.claude/projects/<sanitized-cwd>/*.jsonl
where each line is a JSON object. This script walks the file in order,
extracts user messages + assistant text replies (skipping tool-use noise
unless --include-tools is passed), and writes a clean Markdown document.

Usage:
  python scripts/export_session.py                  # most-recent session in this project
  python scripts/export_session.py PATH.jsonl       # explicit transcript
  python scripts/export_session.py --list           # show recent transcripts
  python scripts/export_session.py --include-tools  # also include tool calls + outputs
  python scripts/export_session.py -o my.md         # custom output path
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import sys


# Claude Code derives this dir from cwd by replacing '/' with '-'
# (e.g. /Users/me/proj/backend → -Users-me-proj-backend)
def _claude_projects_root() -> str:
    return os.path.expanduser("~/.claude/projects")


def _project_dir_for_cwd(cwd: str) -> str:
    sanitized = cwd.replace("/", "-")
    return os.path.join(_claude_projects_root(), sanitized)


def list_transcripts(project_dir: str) -> list[tuple[str, float, int]]:
    """Returns [(path, mtime, size_bytes)] newest-first."""
    if not os.path.isdir(project_dir):
        return []
    rows = []
    for p in glob.glob(os.path.join(project_dir, "*.jsonl")):
        try:
            st = os.stat(p)
            rows.append((p, st.st_mtime, st.st_size))
        except OSError:
            continue
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def find_default_transcript(start_dir: str) -> str | None:
    """Walk up from start_dir to find the matching project dir, then return
    its most-recently-modified .jsonl."""
    cwd = os.path.abspath(start_dir)
    while True:
        candidate = _project_dir_for_cwd(cwd)
        rows = list_transcripts(candidate)
        if rows:
            return rows[0][0]
        parent = os.path.dirname(cwd)
        if parent == cwd:
            return None
        cwd = parent


def _text_from_content(content) -> str:
    """Pull human-readable text out of a content blob (string or list of
    blocks). Returns "" if nothing useful is present."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                t = (block.get("text") or "").strip()
                if t:
                    parts.append(t)
        return "\n\n".join(parts).strip()
    return ""


def _tool_blocks(content) -> list[dict]:
    """Return all tool_use / tool_result blocks in order."""
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)
              and b.get("type") in ("tool_use", "tool_result")]


def render(transcript_path: str, include_tools: bool = False) -> str:
    """Read the .jsonl and produce a Markdown string."""
    lines = []
    with open(transcript_path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue

    out = []
    out.append(f"# Claude Code session")
    out.append("")
    out.append(f"- **Source:** `{transcript_path}`")
    out.append(f"- **Exported:** {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    out.append(f"- **Messages:** {len(lines)}")
    out.append("")
    out.append("---")
    out.append("")

    for entry in lines:
        role = entry.get("type") or entry.get("role")
        # The transcript wraps messages — typically entry["message"] holds the
        # actual {role, content} from the API call.
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        msg_role = msg.get("role") or role

        if msg_role == "user":
            text = _text_from_content(msg.get("content"))
            if text:
                # Suppress system-reminder noise inside user blocks
                clean = "\n".join(
                    ln for ln in text.splitlines()
                    if "<system-reminder>" not in ln
                    and "</system-reminder>" not in ln
                )
                if clean.strip():
                    out.append("## 🧑 User")
                    out.append("")
                    out.append(clean.strip())
                    out.append("")

        elif msg_role == "assistant":
            text = _text_from_content(msg.get("content"))
            if text:
                out.append("## 🤖 Assistant")
                out.append("")
                out.append(text)
                out.append("")
            if include_tools:
                for tb in _tool_blocks(msg.get("content")):
                    if tb.get("type") == "tool_use":
                        name = tb.get("name", "?")
                        inp = tb.get("input") or {}
                        out.append(f"<details><summary>🛠 Tool call: <code>{name}</code></summary>")
                        out.append("")
                        out.append("```json")
                        out.append(json.dumps(inp, indent=2, default=str)[:4000])
                        out.append("```")
                        out.append("")
                        out.append("</details>")
                        out.append("")
                    elif tb.get("type") == "tool_result":
                        result = _text_from_content(tb.get("content"))
                        if result:
                            out.append("<details><summary>📤 Tool result</summary>")
                            out.append("")
                            out.append("```")
                            out.append(result[:4000])
                            out.append("```")
                            out.append("")
                            out.append("</details>")
                            out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Export a Claude Code session to Markdown.")
    ap.add_argument("path", nargs="?", help="Path to a session .jsonl (default: most-recent in this project)")
    ap.add_argument("-o", "--output", help="Output markdown path (default: ./claude-session-<id>.md)")
    ap.add_argument("--list", action="store_true",
                    help="Just list recent transcripts and exit")
    ap.add_argument("--include-tools", action="store_true",
                    help="Include tool calls + outputs in the export")
    args = ap.parse_args()

    if args.list:
        project_dir = _project_dir_for_cwd(os.getcwd())
        rows = list_transcripts(project_dir)
        print(f"Project dir: {project_dir}")
        if not rows:
            print("(no transcripts found)")
            return
        for p, mtime, sz in rows[:10]:
            ts = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  {ts}  {sz/1024:>7.1f} KB  {p}")
        return

    transcript = args.path or find_default_transcript(os.getcwd())
    if not transcript:
        print("Couldn't find a session transcript. Try `--list` or pass the path explicitly.",
              file=sys.stderr)
        sys.exit(2)
    if not os.path.exists(transcript):
        print(f"File not found: {transcript}", file=sys.stderr)
        sys.exit(2)

    output = args.output or os.path.join(
        os.getcwd(),
        f"claude-session-{os.path.splitext(os.path.basename(transcript))[0][:8]}.md",
    )
    md = render(transcript, include_tools=args.include_tools)
    with open(output, "w", encoding="utf-8") as f:
        f.write(md)
    size_kb = os.path.getsize(output) / 1024
    print(f"Wrote {output} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
